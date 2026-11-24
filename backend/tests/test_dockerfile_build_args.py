"""Der Landing-Schalter muss den Docker-Build erreichen.

``VITE_PUBLIC_LANDING`` wird von Vite zur Build-Zeit eingebacken.
Railway gibt Service-Variablen nur als Build-Argument weiter, wenn das
Dockerfile sie als ``ARG`` deklariert — und zwar in der Frontend-Stage
VOR ``npm run build``. Ohne das bliebe der Schalter wirkungslos.
"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def test_public_landing_flag_reaches_frontend_build():
    text = DOCKERFILE.read_text(encoding="utf-8")
    stage = text.split("AS frontend-build", 1)[1].split("\nFROM ", 1)[0]
    build_at = stage.index("RUN npm run build")
    arg_at = stage.index("ARG VITE_PUBLIC_LANDING=false")
    env_at = stage.index("ENV VITE_PUBLIC_LANDING=$VITE_PUBLIC_LANDING")
    assert arg_at < env_at < build_at
