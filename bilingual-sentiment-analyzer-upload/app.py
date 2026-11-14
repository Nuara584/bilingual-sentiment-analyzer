import hashlib
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from pathlib import Path

import pandas as pd
import streamlit as st
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion


st.set_page_config(
    page_title="محلل مشاعر المنشورات",
    page_icon="💬",
    layout="wide",
)

DATA_PATH = Path(__file__).parent / "data" / "social_posts.csv"
UNCERTAINTY_THRESHOLD = 0.60


def detect_language(text):
    """تخمين بسيط للغة لعرضها للمستخدم فقط."""
    arabic_letters = len(re.findall(r"[\u0600-\u06FF]", text))
    english_letters = len(re.findall(r"[A-Za-z]", text))
    return "العربية" if arabic_letters > english_letters else "English"


def read_uploaded_batch(uploaded_file):
    """قراءة التعليقات من صيغ الملفات النصية الشائعة."""
    suffix = Path(uploaded_file.name).suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(uploaded_file)

    if suffix in {".txt", ".md"}:
        content = uploaded_file.getvalue().decode("utf-8-sig")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return pd.DataFrame({"text": lines})

    if suffix == ".json":
        data = json.loads(uploaded_file.getvalue().decode("utf-8-sig"))
        if isinstance(data, dict):
            for key in ["comments", "reviews", "posts", "data"]:
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                data = [data]
        if isinstance(data, list) and data and all(isinstance(item, str) for item in data):
            return pd.DataFrame({"text": data})
        return pd.DataFrame(data)

    if suffix == ".xlsx":
        with ZipFile(uploaded_file) as workbook:
            workbook_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            office_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            package_ns = "http://schemas.openxmlformats.org/package/2006/relationships"

            workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
            relationships_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
            first_sheet = workbook_root.find(f"{{{workbook_ns}}}sheets/{{{workbook_ns}}}sheet")
            if first_sheet is None:
                raise ValueError("ملف Excel لا يحتوي على ورقة بيانات.")

            relationship_id = first_sheet.attrib[f"{{{office_ns}}}id"]
            relationship = next(
                item for item in relationships_root.findall(f"{{{package_ns}}}Relationship")
                if item.attrib.get("Id") == relationship_id
            )
            sheet_path = relationship.attrib["Target"].lstrip("/")
            if not sheet_path.startswith("xl/"):
                sheet_path = posixpath.normpath(posixpath.join("xl", sheet_path))

            shared_strings = []
            if "xl/sharedStrings.xml" in workbook.namelist():
                shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
                for item in shared_root.findall(f"{{{workbook_ns}}}si"):
                    shared_strings.append(
                        "".join(node.text or "" for node in item.iter(f"{{{workbook_ns}}}t"))
                    )

            sheet_root = ET.fromstring(workbook.read(sheet_path))
            rows = []
            for row in sheet_root.findall(f".//{{{workbook_ns}}}sheetData/{{{workbook_ns}}}row"):
                values = {}
                for cell in row.findall(f"{{{workbook_ns}}}c"):
                    match = re.match(r"([A-Z]+)", cell.attrib.get("r", ""))
                    if not match:
                        continue
                    column_index = 0
                    for letter in match.group(1):
                        column_index = column_index * 26 + ord(letter) - ord("A") + 1
                    column_index -= 1

                    value_node = cell.find(f"{{{workbook_ns}}}v")
                    value = value_node.text if value_node is not None else ""
                    if cell.attrib.get("t") == "s" and value:
                        value = shared_strings[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        value = "".join(
                            node.text or "" for node in cell.iter(f"{{{workbook_ns}}}t")
                        )
                    values[column_index] = value

                if values:
                    rows.append([values.get(index, "") for index in range(max(values) + 1)])

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows[1:], columns=rows[0])

    raise ValueError("صيغة الملف غير مدعومة.")


@st.cache_resource(show_spinner="جاري تجهيز النموذج لأول مرة...")
def train_model(data_hash):
    """تقييم النموذج ثم تدريبه على كل المنشورات المتاحة."""
    posts = pd.read_csv(DATA_PATH, encoding="utf-8-sig")

    required_columns = {"text", "label", "language"}
    missing_columns = required_columns.difference(posts.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"الأعمدة الناقصة في ملف البيانات: {missing}")

    posts = posts.dropna(subset=["text", "label", "language"]).copy()
    posts["text"] = posts["text"].astype(str).str.strip()
    posts = posts[posts["text"].ne("")]
    posts = posts[posts["label"].isin(["negative", "positive"])]
    posts = posts[posts["language"].isin(["Arabic", "English"])]

    if posts.empty:
        raise ValueError("ملف البيانات لا يحتوي على منشورات صالحة.")

    split_labels = posts["language"].astype(str) + "_" + posts["label"].astype(str)
    train_posts, test_posts = train_test_split(
        posts,
        test_size=0.2,
        random_state=42,
        stratify=split_labels,
    )

    vectorizer = FeatureUnion(
        [
            (
                "words",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=2,
                    max_features=60000,
                    sublinear_tf=True,
                ),
            ),
            (
                "character_parts",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    min_df=2,
                    max_features=60000,
                    sublinear_tf=True,
                ),
            ),
        ]
    )
    train_numbers = vectorizer.fit_transform(train_posts["text"])
    test_numbers = vectorizer.transform(test_posts["text"])

    model = LogisticRegression(max_iter=1000)
    model.fit(train_numbers, train_posts["label"])
    test_predictions = model.predict(test_numbers)

    test_results = test_posts[["language", "label"]].copy()
    test_results["predicted"] = test_predictions

    evaluation_rows = []
    for language in ["Arabic", "English"]:
        language_results = test_results[test_results["language"] == language]
        if not language_results.empty:
            evaluation_rows.append(
                {
                    "اللغة": "العربية" if language == "Arabic" else "English",
                    "عدد منشورات التقييم": len(language_results),
                    "الدقة": accuracy_score(
                        language_results["label"],
                        language_results["predicted"],
                    ),
                }
            )

    return vectorizer, model, pd.DataFrame(evaluation_rows), len(posts), len(train_posts)


if not DATA_PATH.exists():
    st.error("ملف البيانات غير موجود: data/social_posts.csv")
    st.stop()

try:
    data_hash = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()
    vectorizer, model, evaluation, post_count, training_count = train_model(data_hash)
except Exception as error:
    st.error(f"لم أستطع تجهيز النموذج: {error}")
    st.stop()


st.markdown(
    """
    <style>
    .stApp { background: #f4f7fb; }
    .block-container { max-width: 1120px; padding-top: 2rem; padding-bottom: 3rem; }
    [data-testid="stHeader"] { background: transparent; }
    .hero {
        direction: rtl; text-align: right; padding: 2rem 2.2rem; border-radius: 24px;
        background: linear-gradient(125deg, #123b5d 0%, #176b72 58%, #3b8f83 100%);
        color: white; margin-bottom: 1.2rem; box-shadow: 0 14px 32px #17435a1c;
    }
    .hero-kicker { font-size: .9rem; opacity: .82; margin-bottom: .5rem; }
    .hero h1 { color: white; font-size: 2.15rem; margin: 0 0 .5rem 0; }
    .hero p { color: #e8f5f3; font-size: 1.05rem; margin: 0; }
    [data-testid="stMetric"] {
        background: white; border: 1px solid #e5edf2; padding: 1rem 1.1rem;
        border-radius: 16px; box-shadow: 0 5px 18px #173e5510;
    }
    [data-testid="stMetricLabel"] { color: #587080; }
    [data-testid="stMetricValue"] { color: #123b5d; }
    [data-testid="stTabs"] button { font-size: 1rem; }
    [data-testid="stTextArea"] textarea {
        border-radius: 14px; border-color: #d9e4eb; background: white;
        unicode-bidi: plaintext; text-align: start;
    }
    [data-testid="stFileUploader"] section {
        border: 1px dashed #8db4b7; border-radius: 16px; background: #f9fcfc;
    }
    div.stButton > button, div.stDownloadButton > button {
        border-radius: 12px; min-height: 2.8rem; font-weight: 650;
    }
    div.stButton > button[kind="primary"] {
        background: #176b72; border-color: #176b72;
    }
    .section-note { color: #5c7180; direction: rtl; text-align: right; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <div class="hero-kicker">تحليل عربي وإنجليزي بالذكاء الاصطناعي</div>
      <h1>محلّل مشاعر المنشورات</h1>
      <p>اكتبي تعليقًا أو ارفعي ملفًا، واكتشفي إذا كانت المشاعر إيجابية أو سلبية.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

metric_columns = st.columns(3)
metric_columns[0].metric("منشورات التدريب", f"{training_count:,}")
metric_columns[1].metric("منشورات التقييم", f"{post_count - training_count:,}")
metric_columns[2].metric("اللغات", "العربية والإنجليزية")

st.caption(
    "مصادر التدريب عربية وإنجليزية متعددة. تفاصيل البيانات في data/DATA_SOURCES.md. "
    "التقييم يعرض أداء النموذج على منشورات لم يتدرّب عليها."
)

with st.expander("عرض تقييم النموذج ومصادر البيانات"):
    st.dataframe(
        evaluation.style.format({"الدقة": "{:.1%}"}),
        hide_index=True,
        width="stretch",
    )
    st.caption("التقييم منفصل لكل لغة. المنشورات المحايدة غير داخلة في التصنيف الحالي.")

single_tab, batch_tab = st.tabs(["✍️ تحليل تعليق", "📄 تحليل ملف"])

with single_tab:
    st.markdown("### اكتبي تعليقًا للتحليل")
    st.markdown(
        '<p class="section-note">يدعم التطبيق النصوص العربية والإنجليزية. '
        'إذا كان ترجيح النموذج أقل من 60% سيخبرك أنه غير متأكد.</p>',
        unsafe_allow_html=True,
    )
    comment = st.text_area(
        "التعليق أو المنشور",
        height=150,
        placeholder="مثال: الخدمة كانت ممتازة وسريعة جدًا!",
        label_visibility="collapsed",
        key="single_comment",
    )

    if st.button("حلّلي المشاعر", type="primary", use_container_width=True):
        if not comment.strip():
            st.warning("اكتبي تعليقًا أولًا حتى أتمكن من تحليله.")
        else:
            comment_numbers = vectorizer.transform([comment.strip()])
            prediction = model.predict(comment_numbers)[0]
            score = model.predict_proba(comment_numbers).max()
            language = detect_language(comment)

            with st.container(border=True):
                st.markdown("#### نتيجة التحليل")
                st.caption(f"اللغة المرجّحة: {language} · ترجيح النموذج: {score:.0%}")
                st.progress(float(score), text="درجة ترجيح النموذج")
                if score < UNCERTAINTY_THRESHOLD:
                    st.warning(
                        "النموذج غير متأكد؛ ترجيح الإيجابي والسلبي متقارب. "
                        "هذا لا يعني أن التعليق محايد."
                    )
                elif prediction == "positive":
                    st.success("المشاعر إيجابية 😊")
                else:
                    st.error("المشاعر سلبية 😞")

with batch_tab:
    st.markdown("### حلّلي مجموعة تعليقات مرة واحدة")
    st.markdown(
        '<p class="section-note">ارفعي CSV أو Excel أو JSON، أو ملف TXT/Markdown '
        'يحتوي على تعليق واحد في كل سطر. في الجداول استخدمي عمودًا باسم '
        '<code>text</code> أو <code>review</code> أو <code>comment</code>.</p>',
        unsafe_allow_html=True,
    )
    uploaded_file = st.file_uploader(
        "اختاري ملفًا للتحليل",
        type=["csv", "xlsx", "json", "txt", "md"],
        label_visibility="collapsed",
        key="batch_upload",
    )

    if uploaded_file is not None:
        try:
            batch = read_uploaded_batch(uploaded_file)
            text_column = next(
                (column for column in ["text", "review", "comment"] if column in batch.columns),
                None,
            )

            if text_column is None:
                st.error("أضيفي عمودًا اسمه text أو review أو comment.")
            elif batch.empty:
                st.warning("الملف لا يحتوي على تعليقات.")
            else:
                texts = batch[text_column].fillna("").astype(str).str.strip()
                valid_rows = texts.ne("")
                batch["detected_language"] = texts.map(detect_language)
                batch["predicted_sentiment"] = "empty"
                batch["model_score"] = 0.0

                if valid_rows.any():
                    numbers = vectorizer.transform(texts[valid_rows])
                    probabilities = model.predict_proba(numbers)
                    scores = probabilities.max(axis=1)
                    predictions = model.classes_[probabilities.argmax(axis=1)].astype(object)
                    predictions[scores < UNCERTAINTY_THRESHOLD] = "uncertain"
                    batch.loc[valid_rows, "predicted_sentiment"] = predictions
                    batch.loc[valid_rows, "model_score"] = scores.round(3)

                sentiment_counts = (
                    batch.loc[valid_rows, "predicted_sentiment"]
                    .value_counts()
                    .rename_axis("sentiment")
                    .reset_index(name="posts")
                )
                positive_count = int((batch["predicted_sentiment"] == "positive").sum())
                negative_count = int((batch["predicted_sentiment"] == "negative").sum())
                uncertain_count = int((batch["predicted_sentiment"] == "uncertain").sum())

                st.markdown("#### ملخص النتائج")
                summary_columns = st.columns(3)
                summary_columns[0].metric("إيجابي", positive_count)
                summary_columns[1].metric("سلبي", negative_count)
                summary_columns[2].metric("غير متأكد", uncertain_count)
                if not sentiment_counts.empty:
                    st.bar_chart(sentiment_counts, x="sentiment", y="posts", color="#176b72")
                st.dataframe(batch, width="stretch", hide_index=True)
                st.caption("uncertain يعني أن ترجيح النموذج أقل من 60%، وليس فئة محايدة.")
                st.download_button(
                    "تنزيل النتائج كملف CSV",
                    data=batch.to_csv(index=False).encode("utf-8-sig"),
                    file_name="social_media_sentiment_results.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        except Exception as error:
            st.error(f"تعذرت قراءة الملف: {error}")
