
import os, uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column, declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

DATABASE_URL = os.getenv("DATABASE_URL","sqlite+aiosqlite:///data.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL.split("://",1)[1]
CORS_ORIGINS = os.getenv("CORS_ORIGINS","*")

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()
def now(): return datetime.now(timezone.utc)

class R(Base):
    __tablename__='r'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda:'RID_'+uuid.uuid4().hex[:8])
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class K(Base):
    __tablename__='k'
    restaurant_id: Mapped[str] = mapped_column(String, ForeignKey('r.id'), primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True, index=True)
class O(Base):
    __tablename__='o'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda:uuid.uuid4().hex)
    restaurant_id: Mapped[str] = mapped_column(String, ForeignKey('r.id'))
    title: Mapped[str] = mapped_column(String(200))
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    original_price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    qty_total: Mapped[int] = mapped_column(Integer, default=1)
    qty_left: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

app = FastAPI(title='Foody')
app.add_middleware(CORSMiddleware, allow_origins=['*'] if CORS_ORIGINS=='*' else [x.strip() for x in CORS_ORIGINS.split(',')], allow_methods=['*'], allow_headers=['*'])

@app.on_event('startup')
async def s():
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)

@app.get('/health')
async def health(): return {'ok':True}

async def auth(db: AsyncSession, rid:str, key:str):
    if not key: raise HTTPException(401,'Missing X-Foody-Key')
    row = await db.execute(select(K).where(K.restaurant_id==rid)); row = row.scalar_one_or_none()
    if not row or row.key!=key: raise HTTPException(401,'Invalid X-Foody-Key')

@app.post('/api/v1/merchant/register_public')
async def reg(body:dict):
    t = (body.get('title') or '').strip()
    if not t: raise HTTPException(400,'title required')
    async with SessionLocal() as db:
        r = R(title=t); db.add(r); await db.flush()
        k = K(restaurant_id=r.id, key='KEY_'+uuid.uuid4().hex[:12]); db.add(k)
        await db.commit(); return {'restaurant_id':r.id,'api_key':k.key}

@app.get('/api/v1/merchant/offers')
async def list_offers(request:Request, restaurant_id:str, status:str=Query('active',enum=['active','archived','all'])):
    key = request.headers.get('X-Foody-Key')
    async with SessionLocal() as db:
        await auth(db, restaurant_id, key)
        stmt = select(O).where(O.restaurant_id==restaurant_id)
        if status=='active': stmt = stmt.where(O.archived_at.is_(None))
        if status=='archived': stmt = stmt.where(O.archived_at.is_not(None))
        rows = (await db.execute(stmt)).scalars().all()
        return [{{'id':o.id,'title':o.title,'price_cents':o.price_cents,'original_price_cents':o.original_price_cents,'qty_total':o.qty_total,'qty_left':o.qty_left,'expires_at':o.expires_at.isoformat(),'archived_at':o.archived_at.isoformat() if o.archived_at else None}} for o in rows]

@app.post('/api/v1/merchant/offers')
async def create_offer(request:Request, body:dict):
    rid = body.get('restaurant_id'); key = request.headers.get('X-Foody-Key')
    async with SessionLocal() as db:
        await auth(db, rid, key)
        o = O(restaurant_id=rid, title=body.get('title') or '', price_cents=int(body.get('price_cents') or 0),
              original_price_cents=int(body.get('original_price_cents')) if body.get('original_price_cents') not in (None,'') else None,
              qty_total=int(body.get('qty_total') or 1), qty_left=int(body.get('qty_left') or body.get('qty_total') or 1),
              expires_at=datetime.fromisoformat(body.get('expires_at')).astimezone(timezone.utc))
        if not o.title or o.price_cents<=0: raise HTTPException(400,'invalid offer')
        db.add(o); await db.commit(); return {'id':o.id}

@app.delete('/api/v1/merchant/offers/{oid}')
async def archive_offer(oid:str, request:Request, restaurant_id:str):
    key = request.headers.get('X-Foody-Key')
    async with SessionLocal() as db:
        await auth(db, restaurant_id, key)
        o = await db.get(O, oid)
        if not o or o.restaurant_id!=restaurant_id: raise HTTPException(404,'not found')
        o.archived_at = now(); await db.commit(); return {'ok':True}

@app.post('/api/v1/merchant/offers/{oid}/restore')
async def restore_offer(oid:str, request:Request, restaurant_id:str):
    key = request.headers.get('X-Foody-Key')
    async with SessionLocal() as db:
        await auth(db, restaurant_id, key)
        o = await db.get(O, oid)
        if not o or o.restaurant_id!=restaurant_id: raise HTTPException(404,'not found')
        o.archived_at = None; await db.commit(); return {'ok':True}

@app.get('/api/v1/offers')
async def public_offers(restaurant_id:Optional[str]=None):
    async with SessionLocal() as db:
        stmt = select(O).where(O.archived_at.is_(None), O.qty_left>0, O.expires_at>now())
        if restaurant_id: stmt = stmt.where(O.restaurant_id==restaurant_id)
        rows = (await db.execute(stmt)).scalars().all()
        return [{{'id':o.id,'title':o.title,'price_cents':o.price_cents,'original_price_cents':o.original_price_cents,'qty_total':o.qty_total,'qty_left':o.qty_left,'expires_at':o.expires_at.isoformat()}} for o in rows]
