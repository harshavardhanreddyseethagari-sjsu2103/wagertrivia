# seed_from_api.py
#
# Fetches real trivia questions from the Open Trivia Database (OpenTDB)
# and inserts them into our Question table, transformed to fit our
# two-option schema.
#
# Run with:
#   python3 seed_from_api.py
#
# To seed the PRODUCTION (Render) database instead of local, run:
#   DATABASE_URL="<external-render-url>" python3 seed_from_api.py

import html
import random
import requests

from database import init_db, SessionLocal, Question

OPENTDB_URL = "https://opentdb.com/api.php"
AMOUNT_TO_FETCH = 20   # how many questions to pull and store


def fetch_questions(amount: int) -> list[dict]:
    """
    Calls the OpenTDB API and returns its raw results list.
    type=multiple gives us a correct answer PLUS several wrong ones,
    which we'll trim down to one wrong answer ourselves, since our
    schema only supports two options.
    """
    response = requests.get(OPENTDB_URL, params={
        "amount": amount,
        "type": "multiple",
    })
    response.raise_for_status()   # raises an error if the HTTP request itself failed
    data = response.json()

    # response_code 0 = success. Anything else means the API couldn't
    # fulfill the request as asked (see opentdb docs) — we treat that
    # as a hard failure rather than silently returning nothing.
    if data["response_code"] != 0:
        raise RuntimeError(f"OpenTDB returned response_code {data['response_code']}")

    return data["results"]


def transform_to_two_options(raw_question: dict) -> dict:
    """
    OpenTDB gives one correct_answer + a list of incorrect_answers
    (often 3). We only want TWO options total for our binary betting
    UI, so we pick the correct answer plus exactly ONE randomly chosen
    incorrect answer, then randomly decide which becomes "A" and
    which becomes "B" — otherwise the correct answer would always
    land in the same slot, which a clever player could exploit.

    html.unescape() converts encoded characters like &quot; and &amp;
    back into normal text (" and &) — OpenTDB returns HTML-encoded
    text by default, as seen in the raw API examples.
    """
    correct = html.unescape(raw_question["correct_answer"])
    incorrect_pool = raw_question["incorrect_answers"]
    wrong = html.unescape(random.choice(incorrect_pool))

    options = [correct, wrong]
    random.shuffle(options)   # randomize which slot (A or B) holds the correct answer

    correct_slot = "A" if options[0] == correct else "B"

    return {
        "text": html.unescape(raw_question["question"]),
        "option_a": options[0],
        "option_b": options[1],
        "correct_answer": correct_slot,
    }


def main():
    init_db()
    db = SessionLocal()

    print(f"Fetching {AMOUNT_TO_FETCH} questions from OpenTDB...")
    raw_questions = fetch_questions(AMOUNT_TO_FETCH)

    added = 0
    for raw in raw_questions:
        transformed = transform_to_two_options(raw)

        question = Question(
            text=transformed["text"],
            option_a=transformed["option_a"],
            option_b=transformed["option_b"],
            correct_answer=transformed["correct_answer"],
        )
        db.add(question)
        added += 1

    db.commit()
    print(f"Added {added} questions to the database.")
    db.close()


if __name__ == "__main__":
    main()