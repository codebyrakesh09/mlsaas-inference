from alembic import command
from alembic.config import Config

from . import models
from .database import Base, engine


def main():
    Base.metadata.create_all(engine)
    command.upgrade(Config('alembic.ini'), 'head')


if __name__ == '__main__':
    main()