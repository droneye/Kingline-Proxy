# refferal/__init__.py

import os
from .ref_db import ReferralDB
from .service import ReferralService

# Единственный экземпляр БД и сервиса,
# чтобы удобно было импортировать из хендлеров.
ref_db = ReferralDB(path=os.environ["REFERRAL_DB_PATH"])
referral_service = ReferralService(ref_db)

__all__ = ["ReferralDB", "ReferralService", "ref_db", "referral_service"]
