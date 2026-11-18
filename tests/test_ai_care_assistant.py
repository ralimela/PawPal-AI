"""Tests for retrieval and the Ask PawPal RAG pipeline. All offline (MockClient)."""

import pytest

from ai import reply as R
from ai.care_assistant import REFUSAL_TEXT, answer_question, life_stage, personalize
from ai.llm_client import LLMError, MockClient
from ai.retriever import KnowledgeBase, min_words_needed, normalize_word
from pawpal_system import Owner, Pet


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase("knowledge")


@pytest.fixture
def owner():
    owner = Owner("Jordan")
    owner.add_pet(Pet("Rex", "Dog", 4))
    owner.add_pet(Pet("Mia", "Cat", 2))
    owner.add_pet(Pet("Biscuit", "Dog", 0))
    return owner


# ---------- retrieval ----------

def test_word_normalization_matches_word_forms():
    assert normalize_word("dogs") == normalize_word("dog")
    assert normalize_word("walks") == normalize_word("walk")
    assert normalize_word("feeding") == normalize_word("feed")


def test_possessive_s_is_not_a_question_word():
    # Found by evaluate.py: "cat's" used to add a stray "s" to the question words
    from ai.retriever import query_words
    assert query_words("How often should I trim my cat's nails?") == {"often", "trim", "cat", "nail"}


def test_guardrail_needs_two_words_for_multi_word_questions():
    assert min_words_needed(1) == 1
    assert min_words_needed(2) == 2
    assert min_words_needed(5) == 3


@pytest.mark.parametrize("question, expected_label", [
    ("How often should I feed a cat?", "cat_care.md > Feeding Cats"),
    ("How often should I walk my dog?", "dog_care.md > Walking and Exercise for Dogs"),
    ("Are lilies safe for cats?", "safety.md > Plants That Are Dangerous to Pets"),
    ("Can dogs eat grapes?", "safety.md > Foods That Are Toxic to Pets"),
])
def test_retrieval_finds_the_right_section_first(kb, question, expected_label):
    assert kb.search(question)[0].label == expected_label


@pytest.mark.parametrize("question", [
    "Who won the world cup?", "How do I make pasta?", "What is the capital of France?",
])
def test_off_topic_questions_retrieve_nothing(kb, question):
    assert kb.search(question) == []


# ---------- personalization ----------

def test_life_stage_uses_species_and_age(owner):
    rex, mia, biscuit = owner.pets
    assert (life_stage(rex), life_stage(mia), life_stage(biscuit)) == ("adult", "adult", "puppy")
    assert life_stage(Pet("Old", "Cat", 12)) == "senior"


def test_personalize_swaps_pet_name_for_species(owner):
    search_text, profiles = personalize("How often should I feed Mia?", owner)
    assert search_text == "How often should I feed cat?"
    assert profiles == ["Mia: Cat, 2 years old (adult)"]


# ---------- the RAG pipeline ----------

def test_grounded_answer_with_valid_citation(kb, owner):
    client = MockClient(["Adult cats do well with at least two meals a day [1]."])
    reply = answer_question("How often should I feed Mia?", owner, kb, client)
    assert reply.kind == R.ANSWER
    assert reply.sources == ["[1] cat_care.md > Feeding Cats"]
    # The prompt really contained the retrieved guide text and Mia's profile
    assert "Feeding Cats" in client.prompts[0] and "Mia: Cat, 2 years old (adult)" in client.prompts[0]


def test_no_evidence_refuses_without_calling_the_ai(kb, owner):
    client = MockClient([])  # any call would raise "ran out of replies"
    reply = answer_question("Who won the world cup?", owner, kb, client)
    assert reply.kind == R.REFUSAL
    assert client.prompts == []
    assert reply.trace.has("RETRIEVE")


def test_model_refusal_is_respected(kb, owner):
    client = MockClient([REFUSAL_TEXT])
    reply = answer_question("What should I feed my rabbit?", owner, kb, client)
    assert reply.kind == R.REFUSAL


def test_output_guardrail_blocks_doses_in_answers(kb, owner):
    client = MockClient(["Give Rex 25 mg of it twice a day [1]."])
    reply = answer_question("How often should I walk Rex?", owner, kb, client)
    assert reply.kind == R.DOSAGE
    assert "25 mg" not in reply.message


def test_made_up_citations_are_dropped(kb, owner):
    client = MockClient(["Walk adult dogs every day [1][7]."])
    reply = answer_question("How often should I walk Rex?", owner, kb, client)
    assert reply.kind == R.ANSWER
    assert all(not s.startswith("[7]") for s in reply.sources)
    assert any("don't exist" in line for line in reply.trace.lines())


def test_api_failure_falls_back_to_guide_text(kb, owner):
    client = MockClient([LLMError("quota exceeded")])
    reply = answer_question("How often should I walk Rex?", owner, kb, client)
    assert reply.kind == R.ANSWER
    assert "AI summary unavailable" in reply.message
    assert reply.trace.has("FALLBACK")


def test_offline_mode_without_client_uses_guide_text(kb, owner):
    reply = answer_question("How often should I walk Rex?", owner, kb, client=None)
    assert reply.kind == R.ANSWER
    assert reply.sources == ["[1] dog_care.md > Walking and Exercise for Dogs"]
