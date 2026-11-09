"""Tablas del sistema: equivalencias, pedidos procesados y ajustes."""
from datetime import datetime

from sqlalchemy import (Column, DateTime, Float, Integer, String, Text,
                        create_engine, select)
from sqlalchemy.orm import declarative_base, sessionmaker

import config

connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
Session = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class Equivalencia(Base):
    __tablename__ = "equivalencias"
    id = Column(Integer, primary_key=True)
    woo_id = Column(Integer, index=True)          # ID web (producto o variación)
    sku = Column(String(120), index=True)
    nombre_web = Column(String(250))
    formato = Column(String(20))                  # ovillo, kilo, bobina, 500g, 50g
    nombre_produccion = Column(String(200))
    unidad = Column(String(20))                   # un, k, b, un 500g
    codigo_defontana = Column(String(120), index=True)
    factor = Column(Float, default=1.0)           # 0,5 para bolsas de 500 g
    estado = Column(String(60))


class PedidoWeb(Base):
    __tablename__ = "pedidos_web"
    id = Column(Integer, primary_key=True)
    woo_id = Column(Integer, unique=True, index=True)
    numero = Column(String(40))
    cliente = Column(String(200))
    pago = Column(String(20))                     # flow | transferencia | otro
    total = Column(Float)
    estado = Column(String(30), default="nuevo")  # nuevo | en_drive | por_confirmar | descartado
    empresa = Column(String(60))
    pedido_texto = Column(Text)
    fila_drive = Column(Integer)
    creado = Column(DateTime)
    enviado = Column(DateTime)
    confirmado = Column(DateTime)
    contactado = Column(DateTime)
    nota = Column(Text)


class Ajuste(Base):
    __tablename__ = "ajustes"
    clave = Column(String(80), primary_key=True)
    valor = Column(Text)


def init():
    Base.metadata.create_all(engine)
    # columnas agregadas después de la primera versión
    with engine.begin() as cx:
        for sql in ["ALTER TABLE pedidos_web ADD COLUMN contactado TIMESTAMP"]:
            try:
                cx.exec_driver_sql(sql)
            except Exception:  # ya existe
                pass


def ajustes() -> dict:
    vals = dict(config.DEFAULTS)
    with Session() as s:
        for a in s.scalars(select(Ajuste)):
            vals[a.clave] = a.valor
    return vals


def guardar_ajustes(nuevos: dict):
    with Session() as s:
        for k, v in nuevos.items():
            if k in config.DEFAULTS:
                s.merge(Ajuste(clave=k, valor=v.strip()))
        s.commit()


def lista(valor: str):
    return [x.strip() for x in (valor or "").split(",") if x.strip()]


def ahora():
    return datetime.now()
