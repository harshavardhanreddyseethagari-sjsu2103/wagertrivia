# seed_questions.py
#
# Run this ONCE to populate a few trivia questions to bet on.
#   python3 seed_questions.py

from database import init_db, SessionLocal, Question

init_db()
db = SessionLocal()

questions = [
    Question(text="Which planet is closer to the sun?", option_a="Venus", option_b="Mars", correct_answer="A"),
    Question(text="Which animal is faster?", option_a="Cheetah", option_b="Lion", correct_answer="A"),
    Question(text="Which is the larger ocean?", option_a="Atlantic", option_b="Pacific", correct_answer="B"),
]

for q in questions:
    db.add(q)

db.commit()
print(f"Seeded {len(questions)} questions.")
db.close()