# AtlasPM Model Research Atlas — 30 verified external models/methods (2026-11-22)

**Goal:** Study ≥20 strong, publicly documented ideas from econometrics, neural time-series, portfolio construction, and risk management, and *reimplement/adapt only justified components in AtlasPM's independently trainable architecture*. This is NOT a claim that all 30 architectures are installed, trained, downloaded, merged, or profitable for 2000–2026 equities. Every model has a different task, assumptions, data horizon, and validation regime. Paper performance on electricity/traffic/retail or intraday books **does not imply financial outperformance**. These links point to author/publisher papers or official descriptions found during research; verify repository code licenses separately before importing any source. **No third-party code or checkpoints copied into AtlasPM.**

## Verified research register

**Priority:** A = candidate to incorporate as our own mechanism with daily-adjusted-close data; B = research comparator or experiment requiring more evidence; C = only after additional intraday, macro, cross-asset or rights-cleared training data.  
**Status:** `studied` means a technical design decision is documented, not that the paper was fully reproduced or its results independently replicated.

| # | Model / method and primary reference | Published strength worth learning | AtlasPM adaptation idea; caveat | Priority |
|---:|---|---|---|:---:|
| 01 | **GARCH** — [Bollerslev 1986](https://doi.org/10.1016/0304-4076(86)90063-1) | Recursively persistent volatility/clustering | Past squared returns + persistent shock-response feature; daily time-varying vol is noisy | A |
| 02 | **EGARCH** — [Nelson 1991](https://ideas.repec.org/a/ecm/emetrp/v59y1991i2p347-70.html) | Negative vs positive return shocks differ | Asymmetry of signed past shocks; no exact EGARCH fitting implied | A |
| 03 | **GJR-GARCH** — [Glosten, Jagannathan & Runkle 1993](https://www.minneapolisfed.org/research/working-papers/on-the-relation-between-the-expected-value-and-the-volatility-of-the-nominal-excess-return-on-stocks) | Negative-shock variance asymmetry | Explicit downside RMS and shock frequency; short crises may still be missed | A |
| 04 | **DCC-GARCH** — [Engle 2002](https://doi.org/10.1198/073500102288618487) | Dynamic cross-asset covariance/correlation | Regularized recent pairwise correlation in allocator; **not** a fully estimated DCC-GARCH | A |
| 05 | **HAR-RV** — [Corsi 2009](https://doi.org/10.1093/jjfinec/nbp001) | Daily/weekly/monthly heterogeneous risk memory | Multi-horizon historical risk blocks; we only have daily prices rather than realized intraday vol | A |
| 06 | **CAViaR** — [Engle & Manganelli 2004](https://www.nber.org/papers/w7341) | Direct conditional lower-tail quantile | Future downside forecasting head; **not VaR** unless fitted to quantiles and calibrated | B |
| 07 | **EVT POT** — [McNeil & Frey 2000](https://www.sciencedirect.com/science/article/pii/S0927539800000128) | Model tail exceedances separately from average risk | Tail-validation stress test, not fit on just 5 future daily outcomes | B |
| 08 | **Markov regime switching** — [Hamilton 1989](https://ideas.repec.org/a/ecm/emetrp/v57y1989i2p357-84.html) | Latent persistent business-cycle/regime states | Smooth latent gate and regime-change diagnostics; no calibrated bear-market labels yet | B |
| 09 | **Bayesian online change-point detection** — [Adams & MacKay 2007](https://arxiv.org/abs/0710.3742) | Online belief over structural breaks | Separate risk alarm on surprise jumps, not historical future-crisis labels | B |
| 10 | **Ledoit–Wolf covariance shrinkage** — [Ledoit & Wolf 2004](https://www.sciencedirect.com/science/article/pii/S0047259X03000964) | Regularize unreliable large-asset covariance | Shrink noisy sample correlation to the identity for only 3 assets; **fixed shrinkage is NOT the Ledoit–Wolf estimated shrinkage parameter** | A |
| 11 | **DeepAR** — [Salinas et al. 2017/2020](https://arxiv.org/abs/1704.04110) | Global probabilistic fit across related series | Shared asset-aware training, distributional risk targets | B |
| 12 | **Temporal Fusion Transformer (TFT)** — [Lim et al. 2021](https://research.google/pubs/temporal-fusion-transformers-for-interpretable-multi-horizon-time-series-forecasting/) | Covariate selection, gating, multi-horizon and interpretability | Learned expert gate + explicit explanations/feature ablations; no causal interpretation implied | A |
| 13 | **N-BEATS** — [Oreshkin et al. ICLR 2020](https://www.servicenow.com/research/publication/boris-n.-oreshkin-n-be-iclr2020.html) | Interpretable trend/seasonal basis expansion | Simple linear trend decomposition as comparison rather than 1000s-parameter stack | B |
| 14 | **N-HiTS** — [Challu et al. AAAI 2023](https://arxiv.org/abs/2201.12886) | Hierarchical interpolation and multiscale focus | Fused 5/10/lookback-day feature scales; 20-day features do not recreate original architecture | A |
| 15 | **DLinear** — [Zeng et al. AAAI 2023](https://arxiv.org/abs/2205.13504) | Strong simple decomposed linear baselines | Require minimal no-return and trailing-risk baselines; complexity must beat simple models | A |
| 16 | **TSMixer** — [Chen et al. TMLR 2023](https://research.google/pubs/tsmixer-an-all-mlp-architecture-for-time-series-forecasting/) | Efficient temporal/channel mixing with MLPs | Lightweight cross-scale input mixing; small 3-asset universe | B |
| 17 | **TiDE** — [Das et al. TMLR 2023](https://research.google/pubs/long-horizon-forecasting-with-tide-time-series-dense-encoder/) | Compact dense encoder with temporal context | Plain dense encoder optional comparator; not proven better in noisy finance | B |
| 18 | **TimesNet** — [Wu et al. ICLR 2023](https://mlanthology.org/iclr/2023/wu2023iclr-timesnet/) | Multiple-period 2D variation | Multi-period volatility representation; calendar effects need verification | B |
| 19 | **PatchTST** — [Nie et al. ICLR 2023](https://arxiv.org/abs/2211.14730) | Channel-independent segment/patch representation | Reusable multiscale return patches; our first build is NOT a Transformer | B |
| 20 | **iTransformer** — [Liu et al. ICLR 2024](https://arxiv.org/abs/2310.06625) | Cross-variate token attention | Cross-asset risk co-movement and market-wide shock features | A |
| 21 | **TimeMixer** — [Wang et al. ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/a7ac8a21e5a27e7ab31a5f42a0117bdb-Abstract-Conference.html) | Explicit multiscale past and future mixing | Multiscale risk/downside representations; not identical to PDM/FMM blocks | A |
| 22 | **Chronos** — [Ansari et al. TMLR 2024](https://www.amazon.science/publications/chronos-learning-the-language-of-time-series) | Tokenized distributional pretraining at scale | Evaluate as *separate optional benchmark* only after data/weight licenses and vintage checks | C |
| 23 | **TimesFM** — [Google Research ICML 2024](https://research.google/blog/a-decoder-only-foundation-model-for-time-series-forecasting/) | General pretrained patch-based forecasting | Optional external benchmark, no copying model weights into private Atlas architecture | C |
| 24 | **Moirai** — [Woo et al. ICML 2024](https://proceedings.mlr.press/v235/woo24a.html) | Multi-frequency/multivariate universal forecasting | Study probabilistic multiasset priors; pretraining vintage & licensing critical | C |
| 25 | **Hierarchical Risk Parity (HRP)** — [López de Prado 2016](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678) | Cluster-aware diversified portfolios without unstable inverses | Regularized co-movement diversification; NOT full HRP without clustering/quasi-diagonalization | A |
| 26 | **Black–Litterman** — [Black & Litterman 1992](https://rpc.cfainstitute.org/research/financial-analysts-journal/1992/faj-v48-n5-28) | Regularize uncertain views against neutral market prior | Avoid overconfident return heads; no genuine market-cap prior in three-asset CSV | B |
| 27 | **CVaR optimization** — [Rockafellar & Uryasev 2000](https://www.tandfonline.com/doi/abs/10.21314/jor.2000.038) | Direct expected severe-loss objective | Keep independent historical ES constraint; possible separate portfolio-tail objective | A |
| 28 | **DeepLOB** — [Zhang et al. 2018/2019](https://arxiv.org/abs/1808.03668) | CNN/LSTM patterns in order-book flow | Future execution-risk studies only; daily adjusted-close CSV has **NO order-book data** | C |
| 29 | **Adaptive Conformal Inference (ACI)** — [Gibbs & Candès 2021](https://arxiv.org/abs/2106.00170) | Online interval coverage under shifting distributions | Post-maturity forecast calibration and coverage logging only; no guarantee of conditional tail coverage | B |
| 30 | **TimesFM-3** — [Google Research 2026](https://research.google/blog/timesfm-3-a-zero-shot-foundation-model-for-multivariate-forecasting/) | Multivariate zero-shot quantile forecasts | Future audit comparator after model-vintage/data-license review, **not** an in-house pretrained system | C |

This register deliberately includes forecasting architectures, risk estimators, statistical inference methods and portfolio methods as **different classes**. It would be technically inaccurate to present them as 30 directly substitutable portfolio-return predictors, or say that AtlasPM has trained all 30.

## Technical lessons from the 30-model study

1. **Risk is not only RMS.** GARCH-style clustering and EGARCH/GJR asymmetry motivate multi-scale volatility plus a separate *downside RMS* target. CAViaR/EVT/CVaR motivate later calibrated quantiles and tail-risk budget; downside RMS is **not** a fitted VaR/ES quantile.
2. **Cross-asset exposure matters more than each asset's independent risk.** DCC-style time-varying comovement, Ledoit–Wolf-style regularization, HRP-style diversification and iTransformer's cross-channel viewpoint motivate cross-asset **shrinkage-adjusted** correlation in a simple original portfolio optimizer. The first optimizer is **not** a DCC estimator, HRP or Ledoit–Wolf estimator.
3. **Multi-scale structure without blind scale-up.** HAR-RV, N-HiTS, TimeMixer, and PatchTST motivate 5/10/20-day lookbacks and multiscale features. With only three securities and 759 asset-training rows, copying large Transformers would be high-capacity/low-data overfitting.
4. **Stay calibrated and skeptical.** DLinear/TSMixer/TiDE remind us to require gains over simple baselines, and ACI reminds us to log delayed-maturity forecast errors before adjusting confidence. Foundation models have no automatically verified ability to price trades or account for brokerage execution.
5. **Model proposals cannot override risk.** Native weights, predicted downside, or future latent gate signals are inputs to an **independent** unchanged cash-only 0–1x stress, drawdown, ES and forecast-risk limiter.
6. **Avoid post-selection claims.** Atlas RiskNet v2 had 305.13% historical research cumulative return and 1.031 zero-cash-yield Sharpe on an already-studied 2000–2026 period, but worse crisis losses in 2008/2020/2022 than trailing-volatility allocations. No proof of robust risk-adjusted superiority. The new architecture is a **predeclared experiment**, not a trained profitable system until rerun, and previously studied years remain development data.

## Implemented experiment vs study only

**In v0.7.0 FusionNet experiment:** a from-scratch softmax-gated *dual risk head*, predicting forward overall RMS and *downside* RMS, with 5/10/feature-window historical risk memory, shock asymmetry, cross-asset return co-movement, self-contained normalized training/gradient optimization, and optional shrinkage-aware portfolio allocation. It deliberately draws **conceptual** design inspiration from items 01–05, 10, 12, 14, 20–21, 25 and 27. This **does not** mean those published models are reimplemented or pretrained in FusionNet.

**Not yet implemented:** original full GARCH likelihood or DCC likelihood, calibrated CAViaR/EVT tail distributions, HMM/BOCPD posterior, full HRP tree, TFT/PatchTST/TimesFM/Chronos/Moirai backbones, Black–Litterman posterior, DeepLOB market-data ingestion, predictive coverage guarantees. Keep these as explicitly gated future independent experiments, not hidden ensemble dependencies.

## Experimental acceptance checklist

- No model receives future adjusted prices, even through shared scaling, correlated asset feature windows or supervised forward labels; train labels must fully mature by decision close.
- No unrelated equity-price series joined with misaligned release dates; date/calendar equality on matched benchmark.
- Fixed initial code/feature/loss/shrinkage and risk caps, reviewed before reading 2000–2026 performance. Do NOT tune weights or choose winners on prior inspected 2020 crisis window.
- Same sample, gross cap, shock, ES, fees, slippage and 0.05 safe no-trade band across FusionNet, native RiskNet and traditional baseline. Report downside forecast error separately from total RMS MAE and realized risk.
- Independent post-2026 forward evidence and/or new genuinely unexamined data/assets, version-locked. Market crisis diagnostics are **post-selection** research only.
- Model code itself is ours and can be MIT licensed; third-party paper mathematics can be studied, but copying external implementation code or pretrained checkpoints would require separate license and provenance checks.
- No live/paper brokerage order execution, personal broker account data, leverage above 1x, or institutional readiness claims. Preserve all false live/IBKR risk-gate flags.

## Research-source integrity

All 30 items above have a direct author/publisher paper or institutional source. The listed lessons are **research hypotheses** applied to a different asset domain, not results reproduced by AtlasPM. There is no universal public benchmark that proves any one of these 30 will increase returns on a three-symbol daily portfolio. The honest success criterion is *incremental prospective performance under matched controls*, not how many models are named in the code.
