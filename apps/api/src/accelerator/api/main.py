from accelerator.api.app import create_app
from accelerator.configuration.settings import get_settings


app = create_app(get_settings())
