import argparse
import math
import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from scipy.stats import chi2_contingency


RATING_ORDER = ["Bad", "Medium", "Good"]
GENDER_LABELS = {"M": "Men", "F": "Women", "W": "Women"}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Analyze whether reviews containing a target word have different "
            "rating distributions across gender groups."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a CSV with rating, gender, and comment columns.",
    )
    parser.add_argument(
        "--word",
        required=True,
        help="Target word to search for as a complete word, case-insensitively.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated charts and summary files.",
    )
    return parser.parse_args()


def load_data(filename):
    #Load and validate the input dataset
    df = pd.read_csv(filename)

    required = {"rating", "gender", "comment"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}"
        )

    df = df.copy()
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["gender"] = df["gender"].astype(str).str.strip().str.upper()
    df["comment"] = df["comment"].fillna("").astype(str)

    #Keep only rows that can be analyzed consistently
    df = df[df["rating"].between(0, 5, inclusive="both")]
    df = df[df["gender"].isin(GENDER_LABELS)]
    df["gender_group"] = df["gender"].map(GENDER_LABELS)

    return df


def classify_rating(rating):
    #Match the original project thresholds
    if rating < 2.5:
        return "Bad"
    if rating <= 3.5:
        return "Medium"
    return "Good"


def filter_keyword(df, target_word):
    #Use a full-word, case-insensitive match
    pattern = re.compile(rf"\b{re.escape(target_word)}\b", re.IGNORECASE)
    mask = df["comment"].apply(lambda text: bool(pattern.search(text)))
    return df.loc[mask].copy()


def build_contingency(filtered_df):
    filtered_df["rating_category"] = filtered_df["rating"].apply(classify_rating)

    counts = (
        filtered_df.groupby(["gender_group", "rating_category"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=RATING_ORDER, fill_value=0)
    )

    #Ensure both groups are present for consistent reporting
    counts = counts.reindex(["Men", "Women"], fill_value=0)
    return counts


def normalized_percentages(counts):
    #Normalize within each gender group to make groups comparable
    totals = counts.sum(axis=1).replace(0, pd.NA)
    percentages = counts.div(totals, axis=0).fillna(0) * 100
    return percentages


def cramers_v(contingency):
    #Measure association strength for the contingency table
    values = contingency.to_numpy()
    n = values.sum()

    if n == 0 or min(values.shape) < 2:
        return math.nan

    chi2, _, _, _ = chi2_contingency(values)
    denominator = n * min(values.shape[0] - 1, values.shape[1] - 1)

    if denominator <= 0:
        return math.nan

    return math.sqrt(chi2 / denominator)


def statistical_test(counts):
    #Run a chi-square test when the table has enough nonzero information
    values = counts.to_numpy()

    if values.sum() == 0:
        return math.nan, math.nan, math.nan

    #Drop empty rating columns because chi-square cannot use all-zero columns
    nonzero_columns = values.sum(axis=0) > 0
    reduced = values[:, nonzero_columns]

    if reduced.shape[1] < 2 or (reduced.sum(axis=1) == 0).any():
        return math.nan, math.nan, math.nan

    chi2, p_value, _, _ = chi2_contingency(reduced)
    effect_size = cramers_v(pd.DataFrame(reduced))
    return chi2, p_value, effect_size


def create_chart(percentages, target_word, output_path):
    #Plot normalized percentages rather than raw counts
    ax = percentages.T.plot(kind="bar", figsize=(10, 6))

    ax.set_title(
        f"Rating Distribution for Reviews Containing '{target_word}'"
    )
    ax.set_xlabel("Rating Category")
    ax.set_ylabel("Share of Matching Reviews (%)")
    ax.set_ylim(0, 100)
    ax.legend(title="Professor Gender")
    ax.grid(axis="y", alpha=0.25)

    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close()


def save_summary(counts, percentages, target_word, chi2, p_value, effect_size, output_path):
    rows = []

    for gender_group in counts.index:
        for category in counts.columns:
            rows.append(
                {
                    "target_word": target_word,
                    "gender_group": gender_group,
                    "rating_category": category,
                    "count": int(counts.loc[gender_group, category]),
                    "percentage_within_group": round(
                        float(percentages.loc[gender_group, category]), 2
                    ),
                    "chi_square": None if math.isnan(chi2) else round(chi2, 4),
                    "p_value": None if math.isnan(p_value) else round(p_value, 6),
                    "cramers_v": None if math.isnan(effect_size) else round(effect_size, 4),
                }
            )

    pd.DataFrame(rows).to_csv(output_path, index=False)


def print_report(counts, percentages, target_word, chi2, p_value, effect_size):
    print(f"\nAnalysis for target word: '{target_word}'")
    print("=" * 52)

    for gender_group in counts.index:
        total = int(counts.loc[gender_group].sum())
        print(f"\n{gender_group} ({total} matching reviews)")
        for category in RATING_ORDER:
            count = int(counts.loc[gender_group, category])
            pct = float(percentages.loc[gender_group, category])
            print(f"  {category:<6} {count:>3} ({pct:>6.2f}%)")

    print("\nStatistical comparison")
    if math.isnan(p_value):
        print("  Not enough variation to run a chi-square test.")
    else:
        print(f"  Chi-square: {chi2:.4f}")
        print(f"  p-value:    {p_value:.6f}")
        print(f"  Cramer's V: {effect_size:.4f}")

    print(
        "\nInterpretation note: this tool detects differences in distributions; "
        "it does not establish why those differences exist or prove causal bias."
    )


def main():
    args = parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    df = load_data(input_path)
    filtered = filter_keyword(df, args.word)

    if filtered.empty:
        print(f"No reviews contained the complete word '{args.word}'.")
        return

    counts = build_contingency(filtered)
    percentages = normalized_percentages(counts)
    chi2, p_value, effect_size = statistical_test(counts)

    safe_word = re.sub(r"[^A-Za-z0-9_-]+", "_", args.word.strip().lower())
    chart_path = output_dir / f"{safe_word}_rating_distribution.png"
    summary_path = output_dir / f"{safe_word}_summary.csv"

    create_chart(percentages, args.word, chart_path)
    save_summary(
        counts,
        percentages,
        args.word,
        chi2,
        p_value,
        effect_size,
        summary_path,
    )
    print_report(counts, percentages, args.word, chi2, p_value, effect_size)

    print(f"\nChart saved to:   {chart_path}")
    print(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()
