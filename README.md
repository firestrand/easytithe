# EasyTithe Manager client

Python 3.14 replacement for the original Python 2 adapter by Alex Ortiz-Rosado.
Uses Requests for cookies, verified TLS, bounded reads, and explicit deadlines.
The legacy login/export endpoint contract is covered by offline fixtures; live
EasyTithe availability has not been verified.

```python
import os
from easytithe import EasyTithe

with EasyTithe(os.environ["EASYTITHE_USERNAME"], os.environ["EASYTITHE_PASSWORD"]) as client:
    contributions = client.GetContributions("10/01/2026", "10/03/2026")
```

Construction logs in and configures the account's Custom export format if
necessary, matching the original behavior. Authentication redirects and all
other redirects raise an error; no request is retried. `GetContributions`
returns a list of string dictionaries with Amount, Type, Date, Fund, Name,
Email, Phone, and PersonID fields. Export pages are bounded to 16 MiB by default.
Use smaller date windows or explicitly adjust `max_response_bytes` for larger
reports. `close()` and context managers release the session.

Development: `uv sync --locked --group dev`, then `uv run --locked ruff format
--check .`, `uv run --locked ruff check .`, `uv run --locked ty check`, and
`uv run --locked pytest`. Python 3.14.8 and uv 0.12.22 are pinned.
