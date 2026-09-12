# Results

Full experimental detail behind the summary in the [README](README.md). Every table cites the file that produced it.

**Contents**

- [Stage 1 — Sentiment classification](#stage-1--sentiment-classification)
- [Stage 2 — Complaint taxonomy](#stage-2--complaint-taxonomy)
- [Stage 3 — Retrieval and generation](#stage-3--retrieval-and-generation)
- [Ablation — metadata tagging](#ablation--metadata-tagging)
- [Reproducing everything](#reproducing-everything)
- [Limitations](#limitations)

---

## Stage 1 — Sentiment classification

Five models, one shared stratified held-out set of **128,539 reviews** (20%, seed 42).

*Source: `data/results/model_comparison.json`*

| Model | Accuracy | Macro F1 | Weighted F1 | Precision | Recall |
|---|---|---|---|---|---|
| **DistilBERT (fine-tuned)** | **0.8243** | **0.8244** | 0.8244 | 0.8247 | 0.8245 |
| Logistic Regression | 0.8034 | 0.8033 | 0.8033 | 0.8033 | 0.8033 |
| Naive Bayes | 0.7558 | 0.7580 | 0.7579 | 0.7621 | 0.7557 |
| RoBERTa (pretrained) | 0.6327 | 0.5694 | 0.5709 | 0.6056 | 0.6314 |
| VADER | 0.5142 | 0.4381 | 0.4396 | 0.5213 | 0.5148 |

### Fine-tuning configuration

`distilbert-base-uncased`, 3-class (negative / neutral / positive). 4 epochs, 10,545 s (~2h56m), 157 samples/s, final training loss 0.335.

DistilBERT was chosen over DeBERTa-v3 and RoBERTa for a hardware reason, stated plainly: distillation retains roughly 97% of BERT-base performance at ~40% fewer parameters, which made four epochs over 642k reviews feasible on freely available compute. A larger encoder would likely score higher.

*Source: `models/distilbert-finetuned-final/train_metrics.json`*

| Epoch | Train loss | Val loss | Accuracy | Weighted F1 | Macro F1 |
|---|---|---|---|---|---|
| 1 | 0.2612 | 0.4482 | 83.48% | 83.51% | 82.59% |
| 2 | 0.2810 | **0.3977** | 84.08% | 84.13% | **83.28%** |
| 3 | 0.2355 | 0.4508 | 83.98% | 84.00% | 83.12% |
| 4 | 0.2007 | 0.5225 | 83.73% | 83.71% | 82.80% |

Validation loss bottoms at epoch 2 and rises afterwards while training loss keeps falling — textbook overfitting onset. Best-checkpoint selection on validation macro F1 retains epoch 2's weights, so the extra two epochs cost time and nothing else.

### Per-platform breakdown

600 reviews per platform, balanced. This is the result the aggregate table hides.

*Source: `data/results/sentiment_diagnostics/per_platform_metrics.csv`*

| Model | Amazon | Yelp | Twitter Airline | Spread |
|---|---|---|---|---|
| **DistilBERT (fine-tuned)** | 0.8448 | 0.8301 | 0.8505 | **0.020** |
| Logistic Regression | 0.7908 | 0.7948 | 0.6434 | 0.151 |
| Naive Bayes | 0.7952 | 0.7573 | 0.4838 | 0.311 |
| RoBERTa (pretrained) | 0.6218 | 0.5544 | **0.7853** | 0.231 |
| VADER | 0.4811 | 0.4252 | 0.5880 | 0.163 |

Pretrained RoBERTa comes fourth overall and **first in the entire study** on tweets. It was pretrained on Twitter data, and the advantage does not survive the move to long-form review prose — a 23-point drop between Twitter and Yelp. Naive Bayes shows the mirror image: competitive on Amazon and Yelp, collapsing to 0.4838 on tweets.

The ranking is not stable. It depends on the match between pretraining distribution and target domain, which is exactly what a single aggregate number conceals.

### Statistical significance

McNemar's exact test on paired predictions, balanced diagnostic sample (n = 1,800).

*Source: `data/results/metrics_summary/significance_tests.csv`*

Nine of ten pairwise comparisons are significant at p < 0.05, most by wide margins. DistilBERT against logistic regression holds at **p = 1.7 × 10⁻²¹**.

The exception: **Naive Bayes and pretrained RoBERTa cannot be separated (p = 0.82).** A bag-of-words classifier and a 125M-parameter transformer are statistically indistinguishable on this sample. That does not establish equivalence, but it does mean no ranking claim between them is supportable — and it is reported rather than quietly dropped.

### Isolating the failure: binary re-evaluation

Removing the neutral class and forcing a binary decision.

*Source: `data/results/sentiment_diagnostics/binary_comparison.csv`*

| Model | Three-class macro F1 | Binary (forced) | Gain |
|---|---|---|---|
| DistilBERT (fine-tuned) | 0.8426 | **0.9533** | +0.111 |
| Logistic Regression | 0.7449 | 0.9216 | +0.177 |
| Naive Bayes | 0.6959 | 0.8803 | +0.184 |
| RoBERTa (pretrained) | 0.6669 | **0.9341** | +0.267 |
| VADER | 0.5094 | 0.7974 | +0.288 |

Pretrained RoBERTa moves from 0.6669 to 0.9341, closing almost the entire gap to the fine-tuned model. Its apparent weakness was **neutral-class discrimination, not sentiment understanding**.

Two consequences. First, most of what separates these models on this corpus is one class. Second, since labels derive from star ratings and "neutral" means different things on different platforms, an unknown share of that difficulty belongs to the labels rather than the models.

### Why VADER fails, and why the obvious explanation is wrong

VADER labels **40.4% of genuinely negative reviews positive**. The intuitive fix is threshold tuning, so the neutral band was swept.

Macro F1 peaks at ±0.25 and the recovery is small. The errors are not clustered near the decision boundary — they are confidently on the wrong side of it. Lexicon scoring applies to words rather than to the clauses those words sit in, so a review reading *"worked well until the keys stopped working"* scores positive on vocabulary alone. No threshold recovers that.

---

## Stage 2 — Complaint taxonomy

BERTopic (UMAP + HDBSCAN) run **per platform** rather than on the pooled corpus, because Yelp is 70% of the data and would otherwise dominate the topic space.

*Source: `data/results/category_discovery/discovery_platform_topics.csv`*

| Platform | Documents | Min topic size | Topics discovered |
|---|---|---|---|
| Amazon | 57,931 | 86 | **69** |
| Yelp | 149,838 | 224 | **18** |
| Twitter Airline | 8,890 | 30 | **34** |

Amazon yields nearly four times as many topics as Yelp from a third of the documents. Not a parameter artefact — minimum topic size was scaled to each platform's document count specifically to prevent that. It is semantic breadth: Amazon spans electronics, groceries, clothing and cosmetics, where a failing battery and a stale flavour share almost no vocabulary, so HDBSCAN finds many small dense regions. Yelp is narrow by comparison.

The consequence is that consolidating to a fixed 24 does genuine compression for Amazon and near-relabelling for Yelp — compression ratios differing by almost 4× — so the taxonomy fits Yelp's structure better than Amazon's.

The 121 raw topics were consolidated into a 24-category cross-domain taxonomy, then assigned with DeBERTa-v3 zero-shot entailment over an embedding shortlist of six candidates.

**Evaluation without ground truth** (held-out n = 450, 150 per platform):

| Measure | Value |
|---|---|
| Categories defined / observed in sample | 24 / 21 |
| Mean within-category coherence | 0.6334 |
| Category separation | 0.2831 |
| Unclassified | **124 / 450 (27.6%)** |
| Largest category | Product Performance Failures — 127 / 450 (28.2%) |

Three of the 24 categories drew no reviews at all in the held-out sample, and the smallest observed category drew one.

The 27.6% unclassified rate is unflattering and trustworthy for that reason — the design records a weak match as unclassified rather than forcing it into the nearest bucket, the same philosophy as the retrieval threshold.

**Where it failed.** One category absorbs 28.2% of the sample at among the lowest coherence recorded — the consolidation to a fixed 24 was too aggressive, and the platform asymmetry suggests a hierarchical taxonomy would have served better than a flat one. Separately, the categoriser assigns *positive* reviews to complaint categories, because zero-shot assignment was applied without a preceding sentiment filter. That is a design error the results expose plainly.

---

## Stage 3 — Retrieval and generation

### Retrieval

*Source: `data/results/evaluation_semantic_search/overall_summary.csv`*

| Metric | Value |
|---|---|
| Queries | 30 |
| Mean retrieval time | 21.71 ms |
| Mean similarity | 0.6833 |
| Precision@3 | 0.90 |

Dense retrieval over 642,692 indexed reviews is fast and precise at the top of the ranking. The RAG evaluation below tells a different story about relevance in context.

### RAG against a standalone LLM

Same model (Llama 3.1 8B via Groq), same questions, with and without retrieval. RAGAS metrics, n = 5.

*Source: `data/results/llm_vs_rag/overall_results.csv`*

| Metric | RAG | Standalone LLM |
|---|---|---|
| Answer relevancy | 0.610 | **0.753** |
| Faithfulness | 0.540 | not measurable — no sources |
| Context precision | 0.416 | — |
| Context recall | 0.400 | — |
| Mean latency | 1,583 ms | 734 ms |
| Mean reviews retrieved | 3.4 | — |

**The grounded pipeline scored lower on relevancy while being the only one whose answers trace to evidence.** RAG's contribution here is verifiability, not responsiveness — and that is reported as a trade-off, not a win.

The per-question data qualifies it further: the baseline's advantage rests on four strong scores and one complete failure, giving it both the higher ceiling and the lower floor.

**Three components, three verdicts.** Retrieval is fast and precise at k=3 in isolation. Context precision across the RAG evaluation is 0.416 and context recall 0.400. Generation faithfulness is 0.540. The ceiling is set by retrieval relevance in context, not by the generator — which is why the unrun retrieval-depth ablation is the most consequential gap in the study.

---

## Ablation — metadata tagging

The one design decision that was measured rather than argued. Retrieval was held identical on both arms, so the difference is attributable to the prompt alone.

*Source: `data/results/ablation_metadata_tagging/comparison_summary.csv`*

| Metric | Tagged | Untagged | Δ |
|---|---|---|---|
| Faithfulness | 0.667 | 0.549 | **+0.118** |
| Answer relevancy | 0.452 | 0.654 | **−0.202** |
| Context precision | 0.416 | 0.416 | 0.000 |
| Context recall | 0.400 | 0.400 | 0.000 |
| Mean latency (ms) | 1,705 | 501 | +1,203 |

Tagging helps groundedness and costs responsiveness. The identical precision and recall confirm the retrieval control held.

The honest reading: this validated the mechanism, not the decision. Whether +0.118 faithfulness is worth −0.202 relevancy depends on whether the system is answering questions someone will act on. For a complaint-analysis tool where a wrong conclusion drives a wrong intervention, it is — but that is a judgement, not a measurement, and it should be stated as one.

---

## Reproducing everything

```bash
python scripts/preprocess.py                        # unify the three corpora
python scripts/train_classical_models.py            # NB + LR, tracked in MLflow
# notebooks/3.DistillBERTFineTuning.ipynb           # DistilBERT fine-tuning
python scripts/evaluate_models.py                   # all five, shared held-out set
python evaluate/evaluate_sentiment_diagnostics.py   # per-platform + binary re-eval
python scripts/discover_categories.py               # BERTopic per platform
python evaluate/evaluate_category_discovery.py      # taxonomy evaluation
python scripts/build_index.py                       # FAISS index
python scripts/evaluate_semantic_search.py          # retrieval metrics
python evaluate/evaluate_llm_vs_rag.py              # RAGAS, RAG vs standalone
python evaluate/ablation_metadata_tagging.py        # the ablation
```

Seeds fixed at 42 throughout. Dependencies pinned in `requirements.txt`. Classical model training and evaluation log parameters, metrics, tags and serialised models to MLflow:

```bash
mlflow ui        # http://localhost:5000
```

---

## Limitations

Stated plainly, because they bound what the numbers mean.

**Split contamination on one model.** The fine-tuned DistilBERT was developed against a separately generated corpus version, so part of the shared test set overlaps data it saw. Its clean figure — **0.8375 macro F1 on its own held-out partition**, measured at training time on data it provably never saw — is what the study reports as definitive. The 0.8244 shared-set figure is included for comparability with the other four models, which are unaffected, as are the per-platform findings, the diagnostics, and the taxonomy and RAG stages. In production the control is a single canonical split written to disk with a CI assertion that train and eval ID sets don't intersect.

**Labels come from star ratings.** A four-star review containing a complaint is labelled positive, and "neutral" is not consistently defined across platforms. This bounds every model's ceiling and is the largest single source of the neutral-class difficulty documented above.

**Generation evaluation is n = 5.** Per-question faithfulness spans 0.000 to 1.000 and context precision 0.000 to 1.000. No confidence interval would be meaningful. Those findings support qualitative conclusions and cannot support precise values.

**The judge grades its own work.** RAGAS scores were produced by the same model family that generated the answers. One question failed to parse and was scored on four. An independent evaluator would be a materially stronger design.

**The similarity threshold is unvalidated.** `SIMILARITY_THRESHOLD = 0.35` is a starting point, not a calibrated constant — the code comment says as much. Calibrating it against a labelled probe set of in-scope, out-of-scope and deliberately borderline questions, swept against false-refusal and hallucination rates, is the cheapest outstanding experiment.

**The taxonomy has no ground truth.** The unsupervised measures used instead are contested and partly circular — coherence computed from the same embedding space that produced the clusters.

**Three of four planned ablations were not run.** Retrieval depth, retriever type and prompt design are argued rather than measured. Since Stage 3 identifies retrieval relevance as the binding constraint, those are the most consequential missing experiments.

**A bug the evaluation found by accident.** One question was refused by the scope guard, not by the grounding threshold. Both failure modes produced identical metrics — zero faithfulness, zero precision, zero recall — and only the returned string differed. The structural fix is to log the refusal *reason* as an enum rather than infer it from message text, and to monitor refusal rate segmented by reason. The general lesson is that this evaluation was too small to have found everything, and this case is evidence for that rather than an isolated incident.

**A resource decision with downstream consequences.** Using a free-tier hosted model was a budget choice whose effects propagated further than anticipated — five evaluation questions, no independent judge, three ablations unrun. Budgeting a small amount for API access would have strengthened the evaluation more than any modelling change available within the same effort.
