# Social post sentiment data

`social_posts.csv` currently has 25,905 deduplicated posts: 15,907 Arabic and 9,998 English. The Arabic rows are drawn from two tweet datasets; the English rows come from TweetEval. Neutral English posts were left out because the app currently predicts only positive or negative. The `source` column records which corpus each row came from. Duplicate text was removed, and rows with conflicting labels were excluded.

## Sources

- Arabic posts: [Arabic Sentiment Twitter Corpus](https://huggingface.co/datasets/asas-ai/Arabic_Sentiment_Twitter_Corpus), training split. Source labels `neg` and `pos` were mapped to `negative` and `positive`. The dataset card lists the CDLA-Sharing-1.0 license and identifies the Kaggle source.
- Arabic posts: [Merged MENA Arabic Sentiment Dataset](https://data.mendeley.com/datasets/rgpvg2f8h3/1), a multi-source tweet collection covering MENA and Saudi Arabic. Source labels `0` and `1` were mapped to `negative` and `positive`. The dataset page lists CC BY 4.0. Citation: Abbadi, Mohammad (2025), DOI 10.17632/rgpvg2f8h3.1.
- English posts: [TweetEval sentiment](https://huggingface.co/datasets/cardiffnlp/tweet_eval), training split. Labels `0` (negative) and `2` (positive) were retained; label `1` (neutral) was excluded. The dataset card lists CC BY 3.0 for the sentiment subset. Please retain this attribution and review the dataset license before redistributing the data.

## File format

The app expects `social_posts.csv` with these columns:

- `text`: the social post
- `label`: `negative` or `positive`
- `language`: `Arabic` or `English`

The app splits this sample into training and evaluation portions each time it trains. The reported accuracy measures this sample and should not be read as a guarantee for every social platform, dialect, topic, or sarcastic post.
