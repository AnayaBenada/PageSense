"""BM25 keyword search over a page's chunks.

Embeddings match meaning but can miss questions that hinge on one exact word
(a function name like clone(), a paper title). BM25 scores exact word overlap,
so the two are combined in rag.retrieve.
"""
import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can could do does did for from how i if in into is it its me my of on or "
    "so that the their them then there these they this to was we what when where which who why will "
    "with would you your about should".split()
)


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d)) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df = Counter(w for tf in self.tfs for w in tf)
        n = len(docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        out = []
        for tf, length in zip(self.tfs, self.lens):
            s = 0.0
            for w in terms:
                if f := tf.get(w):
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1)))
            out.append(s)
        return out
