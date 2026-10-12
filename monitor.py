import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from alerts import AlertState, emit_alert, process_foreign_net_buy_alert
from auth import get_access_token


WS_URL = "wss://api.nhplug.com:7070/websocket"
DB = Path("data/market_monitor.db")


def token_value():
    t = get_access_token()
    if isinstance(t, dict):
        return t.get("access_token")
    return t


def init_db():
    DB.parent.mkdir(exist_ok=True)

    con = sqlite3.connect(DB)

    con.execute("""
    CREATE TABLE IF NOT EXISTS foreign_member_flow (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collected_at TEXT NOT NULL,
        code TEXT,
        name TEXT,
        foreign_sell INTEGER,
        foreign_buy INTEGER,
        foreign_net INTEGER,
        foreign_net_change INTEGER,
        total_sell INTEGER,
        total_buy INTEGER,
        raw_json TEXT
    )
    """)

    con.commit()
    con.close()


def save(body):
    def num(k):
        try:
            return int(float(body.get(k, 0) or 0))
        except Exception:
            return 0

    con = sqlite3.connect(DB)

    con.execute("""
    INSERT INTO foreign_member_flow (
        collected_at,
        code,
        name,
        foreign_sell,
        foreign_buy,
        foreign_net,
        foreign_net_change,
        total_sell,
        total_buy,
        raw_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        body.get("code"),
        body.get("hname"),
        num("N_offvolall"),
        num("N_bidvolall"),
        num("N_soonmaesu"),
        num("N_soonmaecha"),
        num("N_alloffvol"),
        num("N_allbidvol"),
        json.dumps(body, ensure_ascii=False)
    ))

    con.commit()
    con.close()


async def subscribe(ws, codes, token):
    for code in codes:
        req = {
            "header": {
                "token": token,
                "tr_type": "1"
            },
            "body": {
                "tr_cd": "mg",
                "tr_key": code
            }
        }

        try:
            await ws.send(json.dumps(req, ensure_ascii=False))
        except (ConnectionClosed, ConnectionClosedError, ConnectionResetError, OSError):
            raise

        await asyncio.sleep(0.12)


async def monitor(codes, seconds=600, max_retries=5):
    init_db()

    if not codes:
        print("[중단] 감시 종목이 없습니다.")
        return

    loop = asyncio.get_running_loop()
    end_time = loop.time() + seconds
    foreign_state = AlertState()
    retry_count = 0

    print(f"[시작] {len(codes)}종목 / {seconds}초")

    while loop.time() < end_time:
        try:
            token = token_value()
            if not token:
                raise RuntimeError("access token을 가져오지 못했습니다.")

            remaining = int(end_time - loop.time())
            print(f"[WebSocket 연결] 남은 감시시간={remaining}초")

            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=20,
            ) as ws:
                await subscribe(ws, codes, token)
                retry_count = 0
                print(f"[구독 완료] {len(codes)}종목")

                while loop.time() < end_time:
                    remaining = end_time - loop.time()
                    if remaining <= 0:
                        break

                    timeout = min(5, max(0.5, remaining))

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    except (ConnectionClosed, ConnectionClosedError, ConnectionResetError, OSError):
                        raise

                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    body = msg.get("body")
                    if not isinstance(body, dict):
                        continue
                    if "N_soonmaesu" not in body:
                        continue

                    save(body)

                    try:
                        foreign_net = int(float((body.get("N_soonmaesu", 0) or 0)))
                    except Exception:
                        foreign_net = 0

                    previous_foreign_net = foreign_state.last_foreign_net

                    if process_foreign_net_buy_alert(
                        foreign_state,
                        foreign_net,
                        previous_foreign_net,
                        threshold=500,
                        ratio_threshold=1.5,
                    ):
                        issue_text = (
                            f"종목: {body.get('hname', body.get('code', ''))}\n"
                            f"코드: {body.get('code', '')}\n"
                            f"외국계 순매수: {foreign_net:,}주\n"
                            f"직전 반영값: {previous_foreign_net:,}주\n"
                            f"수집시각: {datetime.now().isoformat(timespec='seconds')}"
                        )
                        emit_alert("외국계 순매수 급증", issue_text)

                    print(
                        f"[{body.get('time', '')}] "
                        f"{body.get('hname', body.get('code', ''))} "
                        f"외국계순매수={body.get('N_soonmaesu')} "
                        f"변화={body.get('N_soonmaecha')}"
                    )

        except (
            ConnectionClosed,
            ConnectionClosedError,
            ConnectionResetError,
            OSError,
            asyncio.TimeoutError,
        ) as e:
            retry_count += 1

            if loop.time() >= end_time:
                break

            if retry_count > max_retries:
                print(f"[실패] WebSocket 복구 실패 {max_retries}회 초과")
                raise

            wait_time = min(2 ** (retry_count - 1), 16)
            remaining = int(max(0, end_time - loop.time()))

            print(f"[재연결] {type(e).__name__}: {e}")
            print(f"[재연결] 서버 종료 감지, 토큰 재발급 후 재연결 / {retry_count}/{max_retries} {wait_time}초 후 재시도 / 남은시간 {remaining}초")

            await asyncio.sleep(min(wait_time, max(1, int(end_time - loop.time()))))

    print("[종료] 실시간 수급 수집 완료")
