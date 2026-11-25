# Model card: shop_model, version 2

*Owner: the shop's data team. Written for Session 6, Stage 15. Attached to the registered version with `shop-registry card 2 docs/model_card.md`.*

## Intended use

Estimates the probability that a newly listed item will sell, from its price, to help the shop price new listings.

- **Users:** the shop's pricing staff, through the `/predict` API.
- **Not for:** judging staff or customers, setting prices automatically without a person reviewing them, or items outside the shop's catalogue.

## Model

Logistic regression on one feature, the price including VAT at 18 %. Trained with seed 42 by `shop-train`, code version 0.6.0; the run records the exact commit.

## Data

- **Training:** `data/prices_v2.csv`, rows listed from 3 August 2026 to 30 August 2026, after the premium range launched: 60 rows in the window, of which the model is fitted on 48 (`train_model` holds back a random 20 %, as since Stage 7). SHA-256 `3204454704805475aed65f5c535fec764ca3f5c7aeb2a331f2e2d6dcbc839e77`.
- **Evaluation:** the frozen holdout `data/holdout.csv`, 120 items listed from 31 August to 25 October 2026, never trained on. SHA-256 `6f9f166ef89f71275397fdd2a8d5c5f3ce9544a79775944278824963aa072ec1`.
- **Not in the data:** seasons, promotions, item category, stock levels, or any information about buyers.

## Metrics

On the frozen holdout:

| Metric | Value |
|---|---|
| Accuracy (the gate metric) | 0.767 |
| Precision | 0.779 |
| Recall | 0.882 |
| ROC-AUC | 0.829 |

The gate requires accuracy of at least 0.70 on the holdout, as set in `config/config.yaml`; accuracy at 0.70 is the unit's reference choice (Session 5, Section 4.3). On 120 holdout rows the 95 % interval for accuracy is 0.683 to 0.842, so the pass is not secure. Always predicting «will sell» would score 0.633.

For comparison, version 1 (trained from 1 June 2026) scored 0.758: one holdout row fewer, far inside that interval. Version 2 is the champion because its window starts at the premium launch, a choice made before scoring, not because of that difference. A model trained on every row before the holdout scored 0.617 and was not registered.

## Limitations

- One feature only: the model knows nothing about what the item is.
- Fitted on 48 rows, so the estimates are uncertain. Retrain as more weeks arrive.
- It has seen prices from about €10 to €81 including VAT. Above that it extrapolates; the range test checks only that its answers stay probabilities and fall as the price rises.
- Trained on the four weeks after the premium range launched. If buyers change again, the model will not know.

## Monitoring

- The input-price histogram on the Grafana dashboard (Session 4). A sustained shift in the average price is a signal to retrain.
- The `predictions` table records the version that made each prediction (`shop_model/2`), so outcomes can be matched to versions once sales are known.

## Rollback

- **To roll back:** `make champion V=1`. This moves the `champion` alias to version 1 and restarts the API, in about ten seconds, with no rebuild.
- **Who may move the alias:** the model's owner, or the engineer on call. Record the reason in the team's log.
- **Afterwards:** check `/ready` and the newest rows of the `predictions` table, which should both say `shop_model/1`.
