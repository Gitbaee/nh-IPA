import os

def get_credentials():
    """GitHub Secrets 또는 환경변수에서 NH 앱키/시크릿 가져오기"""
    app_key = os.getenv("NH_APP_KEY") or os.getenv("NHPLUG_APP_KEY")
    app_secret = os.getenv("NH_APP_SECRET") or os.getenv("NHPLUG_APP_SECRET")

    if not app_key or not app_secret:
        raise RuntimeError(
            "NH_APP_KEY / NH_APP_SECRET 환경변수가 없습니다. "
            "GitHub Secrets에 등록했는지 확인하세요."
        )
    return app_key, app_secret
