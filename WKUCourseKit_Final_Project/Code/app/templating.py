from pathlib import Path

from fastapi.templating import Jinja2Templates


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)

