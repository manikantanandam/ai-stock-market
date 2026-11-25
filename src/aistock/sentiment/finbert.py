"""Headline sentiment with FinBERT (ProsusAI/finbert), run locally on CPU.

The model (~440MB) downloads once to the Hugging Face cache on first use.
"""
import logging
from datetime import datetime, timezone
from functools import lru_cache

# torch prints a harmless "Redirects are not supported in MacOs" warning on import
logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)

import torch  # noqa: E402
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: E402
from transformers.utils import logging as hf_logging  # noqa: E402

hf_logging.set_verbosity_error()
hf_logging.disable_progress_bar()

MODEL = "ProsusAI/finbert"
HALF_LIFE_DAYS = 3.0  # a headline's weight halves every 3 days


@lru_cache(maxsize=1)
def _model():
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL).eval()
    return tok, model


def classify(texts: list[str]) -> list[dict]:
    """Per text: positive/negative/neutral probabilities and score = pos - neg."""
    if not texts:
        return []
    tok, model = _model()
    labels = [model.config.id2label[i].lower() for i in range(model.config.num_labels)]
    out = []
    for start in range(0, len(texts), 32):
        batch = tok(texts[start:start + 32], padding=True, truncation=True, max_length=64, return_tensors="pt")
        with torch.no_grad():
            probs = model(**batch).logits.softmax(-1).tolist()
        for p in probs:
            d = dict(zip(labels, p))
            d["score"] = d["positive"] - d["negative"]
            out.append(d)
    return out


def sentiment_score(news: list[dict], now: datetime | None = None) -> tuple[float, list[str]]:
    """Recency-weighted mean of headline scores in [-1, 1], with the strongest headlines as reasons.
    now: the moment ages are measured from (a past session's cutoff when backfilling)."""
    if not news:
        return 0.0, ["no recent headlines"]
    now = now or datetime.now(timezone.utc)
    scored = []
    for n, s in zip(news, classify([n["title"] for n in news])):
        age = (now - datetime.fromisoformat(n["published"])).total_seconds() / 86400
        scored.append((n, s["score"], 0.5 ** (max(age, 0) / HALF_LIFE_DAYS)))
    score = sum(s * w for _, s, w in scored) / sum(w for _, _, w in scored)
    pos = sum(s > 0.3 for _, s, _ in scored)
    neg = sum(s < -0.3 for _, s, _ in scored)
    why = [f"{len(scored)} headlines: {pos} positive, {neg} negative, {len(scored) - pos - neg} neutral"]
    for n, s, _ in sorted(scored, key=lambda x: abs(x[1]) * x[2], reverse=True)[:4]:
        if abs(s) > 0.3:
            why.append(f"{s:+.2f} {n['published'][:10]} {n['title']} ({n['source']})")
    return max(-1.0, min(1.0, score)), why
