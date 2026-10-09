from accelerator.api.composition import build_application
from accelerator.configuration.settings import get_settings


app = build_application(get_settings())
