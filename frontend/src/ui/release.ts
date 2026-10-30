// Submission build: which parts of the product are shown. Hiding is reversible and removes no route, code or data.
// Set VITE_FULL_UI=1 (or add ?full=1 to the address) to show everything again.

const full = (): boolean => {
  try { return import.meta.env?.VITE_FULL_UI === "1" || new URLSearchParams(window.location.search).has("full"); } catch { return false; }
};

export const SUBMISSION_MODE = !full();

/** Records made by tests or experiments. Kept in the data folder, left out of the user-facing gallery in the submission build. */
const FIXTURE_NAME = /^(pipe-test|contest-demo|test[-_ ])/i;
export const isFixture = (name: string): boolean => SUBMISSION_MODE && FIXTURE_NAME.test(name.trim());
export const withoutFixtures = <T extends { name: string }>(rows: T[]): T[] => rows.filter((r) => !isFixture(r.name));
