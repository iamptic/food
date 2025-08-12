import os, csv, io, uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from sqlalchemy import func, text, String, Integer, DateTime, Text, ForeignKey, select
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
class FoodyReservation(Base):
    __tablename__ = "foody_reservations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: "RSV_" + uuid.uuid4().hex[:10])
    offer_id: Mapped[str] = mapped_column(String, ForeignKey("foody_offers.id"), index=True)
    restaurant_id: Mapped[str] = mapped_column(String, ForeignKey("foody_restaurants.id"), index=True)
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, default="reserved")
    qty: Mapped[int] = mapped_column(Integer, default=1)
    price_cents_effective: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
        "ALTER TABLE foody_offers ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ",
        "CREATE TABLE IF NOT EXISTS foody_reservations (id VARCHAR PRIMARY KEY, offer_id VARCHAR, restaurant_id VARCHAR, code VARCHAR UNIQUE, status VARCHAR, qty INTEGER, price_cents_effective INTEGER, created_at TIMESTAMPTZ, redeemed_at TIMESTAMPTZ, expires_at TIMESTAMPTZ)"
    ]
    for s in stmts:
        try:
            await conn.execute(text(s))
        except Exception:
            pass
    try:
        await conn.execute(text("UPDATE foody_offers SET qty_total=COALESCE(qty_total,1)"))
        await conn.execute(text("UPDATE foody_offers SET qty_left=COALESCE(qty_left,1)"))
    except Exception:
        pass
    return
 {"ok": True}

# --- Helpers ---
async def _auth_restaurant(db: AsyncSession, restaurant_id: str, api_key: Optional[str]):
    if not restaurant_id: raise HTTPException(400, "restaurant_id required")
    if not api_key: raise HTTPException(401, "Missing X-Foody-Key")
    row = await db.execute(select(FoodyApiKey).where(FoodyApiKey.restaurant_id==restaurant_id))
    row = row.scalar_one_or_none()
    if not row or row.api_key != api_key: raise HTTPException(401, "Invalid X-Foody-Key")

def _offer_dict(o: FoodyOffer):
    tier, eff = price_tier_for_offer(o)
    tl = max(0, int((o.expires_at - now_utc()).total_seconds()//60))
    return {"id": o.id, "restaurant_id": o.restaurant_id, "title": o.title, "description": o.description,
            "price_cents": o.price_cents, "original_price_cents": o.original_price_cents,
            "price_cents_effective": eff, "tier": tier, "qty_total": o.qty_total, "qty_left": o.qty_left,
            "expires_at": o.expires_at.isoformat(), "time_left_min": tl,
            "archived_at": o.archived_at.isoformat() if o.archived_at else None, "created_at": o.created_at.isoformat()}

# --- Tiered discounts ---
def price_tier_for_offer(o: "FoodyOffer"):
    if not o.original_price_cents or o.original_price_cents <= 0:
        return ("base", o.price_cents)
    now = now_utc()
    minutes = int((o.expires_at - now).total_seconds() // 60)
    if minutes <= 30: disc = 0.70; label = "-70%"
    elif minutes <= 60: disc = 0.50; label = "-50%"
    elif minutes <= 120: disc = 0.30; label = "-30%"
    else: return ("base", o.price_cents if o.price_cents else int(o.original_price_cents))
    eff = int(round(o.original_price_cents * (1.0 - disc)))
    return (label, eff)

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

@app.post("/api/v1/buyer/reserve")
async def buyer_reserve(body: dict):
    offer_id = (body.get("offer_id") or "").strip()
    if not offer_id: raise HTTPException(400, "offer_id required")
    async with SessionLocal() as db:
        o = await db.get(FoodyOffer, offer_id)
        if not o or o.archived_at is not None: raise HTTPException(404, "offer not found")
        if o.qty_left <= 0 or o.expires_at <= now_utc(): raise HTTPException(400, "offer not available")
        tier, eff = price_tier_for_offer(o)
        o.qty_left = max(0, o.qty_left - 1)
        code_val = "QR_" + uuid.uuid4().hex[:10].upper()
        rsv = FoodyReservation(offer_id=o.id, restaurant_id=o.restaurant_id, code=code_val, qty=1,
                               price_cents_effective=eff, expires_at=min(o.expires_at, now_utc().replace(microsecond=0) + timedelta(minutes=30)))
        db.add(rsv); await db.commit(); await db.refresh(rsv); await db.refresh(o)
        return {"reservation_id": rsv.id, "code": rsv.code, "status": rsv.status, "expires_at": rsv.expires_at.isoformat(), "offer": _offer_dict(o)}
@app.post("/api/v1/merchant/redeem")
async def merchant_redeem(request: Request, body: dict):
    restaurant_id = (body.get("restaurant_id") or "").strip()
    code_val = (body.get("code") or "").strip()
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        stmt = select(FoodyReservation).where(FoodyReservation.code==code_val)
        rsv = (await db.execute(stmt)).scalar_one_or_none()
        if not rsv: raise HTTPException(404, "reservation not found")
        if rsv.restaurant_id != restaurant_id: raise HTTPException(403, "foreign reservation")
        if rsv.status != "reserved": raise HTTPException(400, "already processed")
        if rsv.expires_at <= now_utc(): 
            rsv.status = "expired"; await db.commit(); raise HTTPException(400, "reservation expired")
        rsv.status = "redeemed"; rsv.redeemed_at = now_utc()
        await db.commit(); await db.refresh(rsv)
        return {"ok": True, "reservation_id": rsv.id, "redeemed_at": rsv.redeemed_at.isoformat()}
@app.get("/api/v1/merchant/reservations")
async def merchant_reservations(request: Request, restaurant_id: str, limit: int = 100):
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        stmt = select(FoodyReservation).where(FoodyReservation.restaurant_id==restaurant_id).order_by(FoodyReservation.created_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        def row(x: FoodyReservation):
            return {"id": x.id, "code": x.code, "status": x.status, "price_cents_effective": x.price_cents_effective,
                    "created_at": x.created_at.isoformat(), "redeemed_at": x.redeemed_at.isoformat() if x.redeemed_at else None,
                    "expires_at": x.expires_at.isoformat(), "offer_id": x.offer_id}
        return [row(x) for x in rows]
@app.get("/api/v1/merchant/kpi")
async def merchant_kpi(request: Request, restaurant_id: str):
    key = request.headers.get("X-Foody-Key")
    async with SessionLocal() as db:
        await _auth_restaurant(db, restaurant_id, key)
        total_reserved = (await db.execute(select(func.count()).select_from(FoodyReservation).where(FoodyReservation.restaurant_id==restaurant_id))).scalar_one()
        total_redeemed = (await db.execute(select(func.count()).select_from(FoodyReservation).where(FoodyReservation.restaurant_id==restaurant_id, FoodyReservation.status=="redeemed"))).scalar_one()
        revenue = (await db.execute(select(func.coalesce(func.sum(FoodyReservation.price_cents_effective),0)).where(FoodyReservation.restaurant_id==restaurant_id, FoodyReservation.status=="redeemed"))).scalar_one()
        stmt = select(FoodyReservation.price_cents_effective, FoodyOffer.original_price_cents).join(FoodyOffer, FoodyOffer.id==FoodyReservation.offer_id).where(FoodyReservation.restaurant_id==restaurant_id, FoodyReservation.status=="redeemed")
        rows = (await db.execute(stmt)).all()
        saved = 0
        for eff, orig in rows:
            if orig and orig>0 and eff is not None:
                saved += max(0, orig - eff)
        rate = (total_redeemed/total_reserved) if total_reserved else 0.0
        return {"reserved": int(total_reserved), "redeemed": int(total_redeemed), "redemption_rate": round(rate,3), "revenue_cents": int(revenue), "saved_cents": int(saved)}

from fastapi.responses import Response
@app.get("/api/v1/qr/{code}.png")
async def qr_png(code: str):
    import qrcode, io as _io
    img = qrcode.make(code)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
