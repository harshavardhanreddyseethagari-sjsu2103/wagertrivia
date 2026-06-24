# database.py
#
# STAGE 3 — adds safe balance updates, plus the schema for trivia
# questions and bets. Two new tables: Question (a question + its
# correct answer) and Bet (a record of one user wagering on one
# question's outcome).

import os
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, Boolean, DateTime, func, CheckConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://harshareddy@localhost:5432/wagertrivia"
)

engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    picture = Column(String, nullable=True)
    balance = Column(Float, nullable=False, server_default="500")

    # Enforced by POSTGRES ITSELF, not Python. Even if some future code
    # path forgets the Python balance check, the database will reject
    # any update that would drive balance below 0 — a hard backstop,
    # independent of application logic.
    __table_args__ = (
        CheckConstraint("balance >= 0", name="balance_non_negative"),
    )

    # relationship() doesn't create a real column — it's a Python-side
    # convenience that lets you write user.bets to get all of a user's
    # bets, WITHOUT writing a separate query yourself. SQLAlchemy
    # figures it out using the foreign key defined on Bet, below.
    bets = relationship("Bet", back_populates="user")


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    text = Column(String, nullable=False)
    option_a = Column(String, nullable=False)
    option_b = Column(String, nullable=False)
    correct_answer = Column(String, nullable=False)   # "A" or "B"
    is_active = Column(Boolean, nullable=False, server_default="false")
    is_resolved = Column(Boolean, nullable=False, server_default="false")

    bets = relationship("Bet", back_populates="question")


class Bet(Base):
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True)

    # ForeignKey: this column STORES a user's id, and Postgres itself
    # enforces that it must match a real row in the users table — you
    # cannot insert a bet pointing at a user that doesn't exist. This
    # is the database enforcing data integrity, not your Python code.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)

    chosen_answer = Column(String, nullable=False)   # "A" or "B"
    wager_amount = Column(Float, nullable=False)
    payout = Column(Float, nullable=True)   # filled in once resolved
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="bets")
    question = relationship("Question", back_populates="bets")


def init_db():
    Base.metadata.create_all(bind=engine)


# ── Safe balance operations ───────────────────────────────────────
def place_bet(db, user_id: int, question_id: int, chosen_answer: str, wager_amount: float):
    """
    Atomically deducts wager_amount from the user's balance AND creates
    a Bet record, as ONE database transaction. If anything fails
    partway through, NOTHING is saved — you can't end up with money
    deducted but no bet recorded, or a bet recorded with no money
    actually deducted.
    """
    user = db.query(User).filter(User.id == user_id).first()

    if user is None:
        raise ValueError("User not found.")
    if wager_amount <= 0:
        raise ValueError("Wager must be positive.")
    if user.balance < wager_amount:
        raise ValueError("Insufficient balance.")

    # The atomic update: tell Postgres to subtract directly, rather
    # than reading user.balance into Python, subtracting, then writing
    # back. User.balance - wager_amount here is SQL, evaluated by
    # Postgres itself at the moment of the update — not by Python.
    db.query(User).filter(User.id == user_id).update(
        {User.balance: User.balance - wager_amount}
    )

    new_bet = Bet(
        user_id=user_id,
        question_id=question_id,
        chosen_answer=chosen_answer,
        wager_amount=wager_amount,
    )
    db.add(new_bet)

    # commit() finalizes BOTH changes (the balance update AND the new
    # bet row) together, as a single transaction.
    db.commit()

    return new_bet


def resolve_question(db, question_id: int, payout_multiplier: float = 1.8):
    """
    Marks a question resolved, pays out everyone who bet correctly.
    payout_multiplier=1.8 means a correct bet returns 1.8x the wager —
    a simple fixed-odds payout, not a pooled pot split (simpler to
    reason about and demo).
    """
    question = db.query(Question).filter(Question.id == question_id).first()
    if question is None or question.is_resolved:
        return

    bets = db.query(Bet).filter(Bet.question_id == question_id).all()

    for bet in bets:
        if bet.chosen_answer == question.correct_answer:
            payout = bet.wager_amount * payout_multiplier
            db.query(User).filter(User.id == bet.user_id).update(
                {User.balance: User.balance + payout}
            )
            bet.payout = payout
        else:
            bet.payout = 0.0

    question.is_resolved = True
    question.is_active = False
    db.commit()