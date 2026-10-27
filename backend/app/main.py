from fastapi import FastAPI
from .database import Base, engine
from . import models

Base.metadata.create_all(engine)
app = FastAPI(title='FlowDesk')

@app.get('/health')
def health():
    return {'status': 'ok'}