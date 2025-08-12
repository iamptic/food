import os, csv, io, uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from sqlalchemy import text, String, Integer, DateTime, Text, ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column, declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# --- ENV & engine ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL.split("://",1)[1]

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
RUN_MIGRATIONS = os.getenv("RUN_MIGRATIONS","1") == "1"

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

def now_utc(): return datetime.now(timezone.utc)

def _parse_client_datetime(s: str) -> datetime:
    # Accept 'YYYY-MM-DDTHH:MM' (naive, local) or full ISO; convert to UTC
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return now_utc()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --- Models ---
class FoodyRestaurant(Base):
    __tablename__ = "foody_restaurants"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: "RID_" + uuid.uuid4().hex[:8])
    title: Mapped[str] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class FoodyApiKey(Base):
    __tablename__ = "foody_api_keys"
    restaurant_id: Mapped[str] = mapped_column(String, ForeignKey("foody_restaurants.id"), primary_key=True)
    api_key: Mapped[str] = mapped_column(String, unique=True, index=True)

class FoodyOffer(Base):
    __tablename__ = "foody_offers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    restaurant_id: Mapped[str] = mapped_column(String, ForeignKey("foody_restaurants.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer)
    original_price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    qty_total: Mapped[int] = mapped_column(Integer, default=1)
    qty_left: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

# --- App ---
app = FastAPI(title="Foody Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS.split(",")] if CORS_ORIGINS!="*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Lightweight migrations for Postgres (one-off safety) ---

# --- Lightweight migrations for Postgres (safety for early schemas) ---
async def _auto_migrate(conn):
    stmts = [
        "ALTER TABLE foody_restaurants ADD COLUMN IF NOT EXISTS phone VARCHAR(50)",
        "ALTER TABLE foody_offers ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE foody_offers ADD COLUMN IF NOT EXISTS original_price_cents INTEGER",
        "ALTER TABLE foody_offers ADD COLUMN IF NOT EXISTS qty_total INTEGER",
        "ALTER TABLE foody_offers ADD COLUMN IF NOT EXISTS qty_left INTEGER",
        "ALTER TABLE foody_offers ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
        "ALTER TABLE foody_offers ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ"
    ]
    for s in stmts:
        try:
            await conn.execute(text(s))
        except Exception:
            pass
    # backfill required ints to sane defaults
    try:
        await conn.execute(text("UPDATE foody_offers SET qty_total=COALESCE(qty_total,1)"))
        await conn.execute(text("UPDATE foody_offers SET qty_left=COALESCE(qty_left,1)"))
    except Exception:
        pass
@app.on_event("startup")
async def on_startup():
    if RUN_MIGRATIONS:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            try:
                await _auto_migrate(conn)
            except Exception:
                pass
            try:
                await _auto_migrate(conn)
            except Exception:
                pass

@app.get("/health")
async def health(): return {"ok": True}

# --- Helpers ---
async def _auth_restaurant(db: AsyncSession, restaurant_id: str, api_key: Optional[str]):
    if not restaurant_id: raise HTTPException(400, "restaurant_id required")
    if not api_key: raise HTTPException(401, "Missing X-Foody-Key")
    row = await db.execute(select(FoodyApiKey).where(FoodyApiKey.restaurant_id==restaurant_id))
    row = row.scalar_one_or_none()
    if not row or row.api_key != api_key: raise HTTPException(401, "Invalid X-Foody-Key")

def _offer_dict(o: FoodyOffer):
    return {
        "id": o.id,
        "restaurant_id": o.restaurant_id,
        "title": o.title,
        "description": o.description,
        "price_cents": o.price_cents,
        "original_price_cents": o.original_price_cents,
        "qty_total": o.qty_total,
        "qty_left": o.qty_left,
        "expires_at": o.expires_at.isoformat(),
        "archived_at": o.archived_at.isoformat() if o.archived_at else None,
        "created_at": o.created_at.isoformat(),
    }

# --- Public endpoints ---
@app.post("/api/v1/merchant/register_public")
async def register_public(body: dict):
    title = (body.get("title") or "").strip()
    phone = (body.get("phone") or "").strip() or None
    if not title: raise HTTPException(400, "title required")
    async with SessionLocal() as db:
        r = FoodyRestaurant(title=title, phone=phone)
        db.add(r); await db.flush()
        key = FoodyApiKey(restaurant_id=r.id, api_key="KEY_" + uuid.uuid4().hex[:12])
        db.add(key); await db.commit()
        return {"restaurant_id": r.id, "api_key": key.api_key}

@app.get("/api/v1/offers")
async def buyer_offers(restaurant_id: Optional[str] = None, limit: int = 100):
    async with SessionLocal() as db:
        stmt = select(FoodyOffer).where(FoodyOffer.archived_at.is_(None), FoodyOffer.qty_left>0, FoodyOffer.expires_at>now_utc())
        if restaurant_id:
            stmt = stmt.where(FoodyOffer.restaurant_id == restaurant_id)
        stmt = stmt.order_by(FoodyOffer.expires_at.asc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return [_offer_dict(o) for o in rows]


# --- Merchant profile ---
@app.get("/api/v1/merchant/profile")
async def merchant_get_profile(request: Request, restaurant_id: str):
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        r = await db.get(FoodyRestaurant, restaurant_id)
        if not r: raise HTTPException(404, "Restaurant not found")
        return {"id": r.id, "title": r.title, "phone": r.phone}

@app.post("/api/v1/merchant/profile")
async def merchant_update_profile(request: Request, body: dict):
    restaurant_id = body.get("restaurant_id")
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        r = await db.get(FoodyRestaurant, restaurant_id)
        if not r: raise HTTPException(404, "Restaurant not found")
        title = (body.get("title") or "").strip()
        phone = (body.get("phone") or "").strip() or None
        if title: r.title = title
        r.phone = phone
        await db.commit(); await db.refresh(r)
        return {"id": r.id, "title": r.title, "phone": r.phone}

# --- Merchant endpoints ---
@app.get("/api/v1/merchant/offers")
async def merchant_list_offers(request: Request, restaurant_id: str, status: str = Query("active", enum=["active","archived","all"])):
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        stmt = select(FoodyOffer).where(FoodyOffer.restaurant_id==restaurant_id)
        if status=="active":
            stmt = stmt.where(FoodyOffer.archived_at.is_(None))
        elif status=="archived":
            stmt = stmt.where(FoodyOffer.archived_at.is_not(None))
        rows = (await db.execute(stmt)).scalars().all()
        return [_offer_dict(o) for o in rows]

@app.post("/api/v1/merchant/offers")
async def merchant_create_offer(request: Request, body: dict):
    restaurant_id = body.get("restaurant_id")
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        o = FoodyOffer(
            restaurant_id=restaurant_id,
            title=(body.get("title") or "").strip(),
            description=(body.get("description") or None),
            price_cents=int(body.get("price_cents") or 0),
            original_price_cents=int(body["original_price_cents"]) if body.get("original_price_cents") not in (None,"") else None,
            qty_total=int(body.get("qty_total") or 1),
            qty_left=int(body.get("qty_left") or body.get("qty_total") or 1),
            expires_at=datetime.fromisoformat(body.get("expires_at")).astimezone(timezone.utc),
        )
        if not o.title or o.price_cents<=0: raise HTTPException(400,"invalid offer")
        db.add(o); await db.commit(); await db.refresh(o)
        return _offer_dict(o)

@app.patch("/api/v1/merchant/offers/{offer_id}")
async def merchant_patch_offer(offer_id: str, request: Request, body: dict):
    restaurant_id = body.get("restaurant_id")
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        o = await db.get(FoodyOffer, offer_id)
        if not o or o.restaurant_id!=restaurant_id: raise HTTPException(404,"Offer not found")
        for k in ["title","description","price_cents","original_price_cents","qty_total","qty_left","expires_at"]:
            if k in body and body[k] is not None:
                if k.endswith("_cents") or k.startswith("qty"):
                    setattr(o,k,int(body[k]))
                elif k=="expires_at":
                    setattr(o,k, _parse_client_datetime(body[k]))
                else:
                    setattr(o,k, body[k])
        await db.commit(); await db.refresh(o)
        return _offer_dict(o)

@app.delete("/api/v1/merchant/offers/{offer_id}")
async def merchant_archive_offer(offer_id: str, request: Request, restaurant_id: str):
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        o = await db.get(FoodyOffer, offer_id)
        if not o or o.restaurant_id!=restaurant_id: raise HTTPException(404,"Offer not found")
        o.archived_at = now_utc()
        await db.commit()
        return {"ok": True, "archived_id": offer_id}

@app.post("/api/v1/merchant/offers/{offer_id}/restore")
async def merchant_restore_offer(offer_id: str, request: Request, restaurant_id: str):
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        o = await db.get(FoodyOffer, offer_id)
        if not o or o.restaurant_id!=restaurant_id: raise HTTPException(404,"Offer not found")
        o.archived_at = None
        await db.commit()
        return {"ok": True, "restored_id": offer_id}

@app.get("/api/v1/merchant/export.csv", response_class=PlainTextResponse)
async def merchant_export_csv(request: Request, restaurant_id: str):
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        stmt = select(FoodyOffer).where(FoodyOffer.restaurant_id==restaurant_id).order_by(FoodyOffer.created_at.desc())
        rows = (await db.execute(stmt)).scalars().all()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id","title","price_cents","original_price_cents","qty_total","qty_left","expires_at","archived_at","created_at"])
        for o in rows:
            w.writerow([o.id,o.title,o.price_cents,o.original_price_cents or "",o.qty_total,o.qty_left,o.expires_at.isoformat(),o.archived_at.isoformat() if o.archived_at else "",o.created_at.isoformat()])
        buf.seek(0)
        return PlainTextResponse(buf.read(), media_type="text/csv")
