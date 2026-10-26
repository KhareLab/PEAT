/**
 * Does the page actually work in a browser?
 *
 * Nothing used to answer that. `tsc` type-checks a blank page happily, and the
 * Python suite stops at the SSE wire. The failure that prompted this was a dead
 * tab: a branch switch deleted two modules under the running dev server, the
 * browser applied an HMR update for a module whose dependency had vanished, and
 * the React tree threw. Which is why **every test here fails on an uncaught page
 * error** — that one assertion is the whole point.
 *
 * The first test replays a canned stream, so it is deterministic and needs no
 * backend. The second does the real round trip and runs only with E2E_LIVE=1,
 * the same opt-in shape as the `live`/`remote`/`llm` tiers in pytest.
 */

import { expect, test, type Page } from "@playwright/test";

const SUMMARY = "Goal: raise thermostability\n\nReference: 1OIL (STRUCTURE OF LIPASE).";

const TRACE = [
  { node: "coordinator", ms: 4, goto: "orchestrator", intent: "design" },
  { node: "orchestrator", ms: 967, goto: "analyst", round: 1, n_worklist: 7 },
  {
    node: "interpreter",
    ms: 177,
    goto: "end",
    reply_source: "interpreter:_rule_based_summary",
    n_messages: 1,
  },
];

const ARTIFACT = {
  id: "a1",
  kind: "markdown",
  title: "1OIL session summary",
  url: "/api/artifacts/a1",
};

/** The SSE body a rules-only design turn produces. */
function cannedStream(): string {
  const frames = [
    { type: "status", text: "Reading your request…", node: "coordinator" },
    { type: "status", text: "Summarizing the session…", node: "interpreter" },
    { type: "state", node: "analyst", state: { round: 1, artifacts: [ARTIFACT] } },
    {
      type: "state",
      node: "interpreter",
      state: { trace: TRACE, reply_source: "interpreter:_rule_based_summary" },
    },
    {
      type: "message",
      text: SUMMARY,
      node: "interpreter",
      source: "interpreter:_rule_based_summary",
    },
    { type: "done" },
  ];
  return frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
}

/** Fail the test on any uncaught exception or failed request from the page. */
function guard(page: Page): string[] {
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console.error: ${message.text()}`);
  });
  return problems;
}

async function send(page: Page, text: string) {
  const box = page.getByRole("textbox");
  await box.fill(text);
  await box.press("Enter");
}

test("a turn renders, and says how it was made", async ({ page }) => {
  const problems = guard(page);

  await page.route("**/api/**", async (route) => {
    const url = route.request().url();
    if (url.includes("/api/chat")) {
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: cannedStream(),
      });
    }
    if (url.includes("/api/artifacts/a1")) {
      return route.fulfill({ status: 200, body: "## Interpretation\n\nThe lead is d-3." });
    }
    if (url.includes("/api/health")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, llm: false, hpc: false, notes: [] }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ state: {}, messages: [], tasks: [] }),
    });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "PEAT" })).toBeVisible();

  await send(page, "redesign 1OIL for thermostability");

  // The reply itself.
  await expect(page.getByText("Reference: 1OIL")).toBeVisible();

  // The disclosure: collapsed, then the node path and the author.
  const toggle = page.getByRole("button", { name: /how this answer was made/ });
  await expect(toggle).toBeVisible();
  await expect(page.getByText("orchestrator")).toHaveCount(0);
  await toggle.click();
  await expect(page.getByText("orchestrator").first()).toBeVisible();
  await expect(page.getByText("interpreter:_rule_based_summary")).toBeVisible();
  await expect(page.getByText(/a rule, no model involved/)).toBeVisible();

  // The artifact pane opened on the state frame that carried one.
  await expect(page.getByText("1OIL session summary").first()).toBeVisible();

  expect(problems).toEqual([]);
});

test("the settings panel opens and shows no secret", async ({ page }) => {
  const problems = guard(page);

  await page.route("**/api/me", async (route) =>
    route.fulfill({ json: { auth: false } }),
  );
  await page.route("**/api/settings", async (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        credentials: {
          llm: {
            anthropic_api_key: { present: true, hint: "sk-ant-…4f2a", source: "env" },
            model: { value: "claude-sonnet-5-5", source: "default" },
            max_tokens: { value: 2048, source: "default" },
          },
          orbit: {},
          globus: {},
          fold: { fold_backend: { value: "esmatlas", source: "default" } },
        },
        overrides: [],
        hpc_available: false,
        llm_available: true,
        persistent_sessions: true,
      }),
    }),
  );
  await page.route("**/api/health", async (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, llm: false, hpc: false, notes: [] }),
    }),
  );
  await page.route("**/api/sessions/**", async (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ state: {}, messages: [], tasks: [] }),
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Settings" }).click();

  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog).toBeVisible();
  const key = dialog.getByLabel(/Anthropic API key/);
  await expect(key).toHaveValue("");
  await expect(key).toHaveAttribute("placeholder", "sk-ant-…4f2a");

  expect(problems).toEqual([]);
});

test("a real turn, end to end", async ({ page }) => {
  test.skip(process.env.E2E_LIVE !== "1", "set E2E_LIVE=1: this one needs the network");
  const problems = guard(page);

  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).click();
  await send(page, "load PDB 1OIL");

  // Through the Vite proxy, the real backend, and RCSB/UniProt/EuropePMC.
  await expect(page.getByText(/Loaded 1OIL/)).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: /how this answer was made/ }).click();
  await expect(page.getByText("initializer").first()).toBeVisible();
  await expect(page.getByText("initializer:summary_line")).toBeVisible();

  expect(problems).toEqual([]);
});
