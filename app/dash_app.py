import requests
import pandas as pd
from dash import Dash, dcc, html, Input, Output
import plotly.express as px
from app.authentication import TokenClient
from config import API_URL

token_client = TokenClient(api_url=API_URL)


def fetch(endpoint: str, params=None, retries=1):
    try:
        token = token_client.get_token()
        headers = {"Authorization": token}
        r = requests.get(
            f"{API_URL}{endpoint}", params=params, headers=headers, timeout=10
        )
        r.raise_for_status()
        return pd.DataFrame(r.json())
    except requests.HTTPError as e:
        if retries > 0 and e.response.status_code == 401:
            # token max be expired, refresh and retry
            token_client.get_token(force_refresh=True)
            return fetch(endpoint, params=params, retries=retries - 1)
        raise


# if token is expired, it will be refreshed and the request will be retried
def fetch_json(endpoint: str, params=None, retries=1):
    try:
        token = token_client.get_token()
        headers = {"Authorization": token}
        r = requests.get(
            f"{API_URL}{endpoint}", params=params, headers=headers, timeout=10
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if retries > 0 and e.response.status_code == 401:
            token_client.get_token(force_refresh=True)  # refresh token
            return fetch_json(endpoint, params=params, retries=retries - 1)
        raise


def fig_jobs_per_day():
    df = fetch("/analytics/jobs-per-day")
    return px.line(
        df,
        x="day",
        y="jobs",
        markers=True,
        labels={"day": "Day", "jobs": "Number of offers"},
    )


def fig_bar(endpoint, x, y):
    df = fetch(endpoint)
    return px.bar(df, x=x, y=y, labels={x: "", y: "Number of offers"})


def fig_top_skills_per_category(selected_category=None):
    df = fetch("/analytics/top-skills")
    if selected_category:
        df = df[df["category"] == selected_category]
    top10 = (
        df.groupby("category")
        .apply(lambda g: g.nlargest(10, "count"))
        .reset_index(drop=True)
    )
    return px.bar(
        top10,
        x="count",
        y="skill",
        color="category",
        facet_col="category",
        facet_col_wrap=2,
        height=750,
        labels={"count": "Occurrences", "skill": "Skill"},
        title="Top 10 Skills per Category",
    )


dash_app = Dash(
    __name__,
    title="Job Dashboard",
    update_title="Loading…",
    suppress_callback_exceptions=True,
)
server = dash_app.server

dash_app.layout = html.Div(
    [
        html.H1("Job Market Dashboard", className="m-4 text-2xl"),
        dcc.Tabs(
            id="tabs",
            value="per-day",
            children=[
                dcc.Tab(label="Offers / day", value="per-day"),
                dcc.Tab(label="Categories", value="cat"),
                dcc.Tab(label="Locations", value="loc"),
                dcc.Tab(label="Companies", value="comp"),
                dcc.Tab(label="Top Skills per Category", value="skills"),
                dcc.Tab(label="Search Offers", value="search"),
            ],
        ),
        html.Div(
            id="search-controls",
            children=[
                html.Div(
                    [
                        dcc.Input(
                            id="search-query",
                            type="text",
                            placeholder="Search offers...",
                            debounce=True,
                            className="p-2 border rounded w-full",
                        ),
                        html.Button(
                            "Search",
                            id="search-btn",
                            className="p-2 m-2 border rounded",
                        ),
                    ],
                    className="m-4 w-2/3",
                ),
                html.Div(id="search-results", className="m-4"),
            ],
            style={"display": "none"},
        ),
        dcc.Graph(id="graph"),
        html.Div(
            [
                html.Label("Select Category:"),
                dcc.Dropdown(id="category-filter", placeholder="All categories"),
            ],
            className="m-4 w-1/2",
        ),
        html.Button(
            "Refresh", id="refresh", n_clicks=0, className="p-2 m-4 border rounded"
        ),
    ]
)


@dash_app.callback(
    Output("graph", "figure"),
    [
        Input("tabs", "value"),
        Input("refresh", "n_clicks"),
        Input("category-filter", "value"),
    ],
)
def update(tab, _, selected_category):
    if tab == "per-day":
        return fig_jobs_per_day()
    elif tab == "cat":
        return fig_bar("/analytics/jobs-by-category", "category", "jobs")
    elif tab == "loc":
        return fig_bar("/analytics/jobs-by-location", "location", "jobs")
    elif tab == "comp":
        return fig_bar("/analytics/top-companies", "company", "jobs")
    elif tab == "skills":
        return fig_top_skills_per_category(selected_category)
    return {}


@dash_app.callback(Output("category-filter", "options"), Input("tabs", "value"))
def populate_dropdown(tab):
    if tab != "skills":
        return []
    df = fetch("/analytics/top-skills")
    categories = sorted(df["category"].unique())
    return [{"label": c, "value": c} for c in categories]


# search in ES
@dash_app.callback(Output("search-controls", "style"), Input("tabs", "value"))
def toggle_search_controls(tab):
    return {"display": "block"} if tab == "search" else {"display": "none"}


@dash_app.callback(
    Output("search-results", "children"),
    [Input("search-btn", "n_clicks"), Input("search-query", "value")],
    prevent_initial_call=True,
)
def perform_search(_, query):
    if not query or len(query) < 2:
        return html.Div(
            "Please enter at least 2 characters.", className="text-red-600 m-2"
        )

    try:
        offers = fetch_json("/search/offers", params={"q": query})
    except Exception as e:
        return html.Div(f"Search failed: {e}", className="text-red-600 m-2")

    if not offers:
        return html.Div("No offers found.", className="m-2")

    cards = []
    for offer in offers:
        highlight = offer.get("highlight", {})
        name_fragments = highlight.get("name", [offer["title"]])
        desc_fragments = highlight.get("cleaned_description", [])
        skills = offer.get("skills", [])
        company = offer.get("company", "")
        categories = ", ".join(offer.get("categories", []))
        locations = ", ".join(offer.get("locations", []))
        url = offer.get("url", "")

        card = html.Div(
            [
                html.H4(
                    name_fragments[0].replace("<em>", "**").replace("</em>", "**"),
                    className="text-xl font-bold",
                ),
                html.Div(f"Company: {company}"),
                html.Div(f"Categories: {categories}"),
                html.Div(f"Locations: {locations}"),
                html.Div(f"Skills: {', '.join(skills)}"),
                html.Div(
                    [
                        html.Strong("Description: "),
                        dcc.Markdown(
                            "... "
                            + " ... ".join(desc_fragments)
                            .replace("<mark>", "**")
                            .replace("</mark>", "**")
                        ),
                    ]
                ),
                html.A(
                    "View Offer",
                    href=url,
                    target="_blank",
                    className="text-blue-600 underline",
                ),
                html.Hr(),
            ],
            className="p-3 m-2 border rounded shadow",
        )

        cards.append(card)

    return cards


if __name__ == "__main__":
    dash_app.run(host="0.0.0.0", port=8050, debug=True)
