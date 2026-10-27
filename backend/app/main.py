from fastapi import FastAPI
from .database import Base, engine
from . import models
from .routers import auth, organizations

Base.metadata.create_all(engine)
app = FastAPI(title='FlowDesk')

app.include_router(auth.router)
app.include_router(organizations.router)

@app.get('/health')
def health():
    return {'status': 'ok'}

