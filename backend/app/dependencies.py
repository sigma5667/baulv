from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

# Re-export for convenient import
DbSession = Depends(get_db)
