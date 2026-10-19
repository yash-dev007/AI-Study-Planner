/**
 * Recovery strategies accepted by the planner.
 *
 * The API only accepts the two explicit strategies; "automatic" is resolved
 * server-side from the course setting `preserve_future_lectures`, so the UI
 * preselects the strategy that matches that setting.
 */

export const STRATEGY_OPTIONS = [
  { value: "preserve_future", label: "Keep future lectures in place (recommended)" },
  { value: "redistribute_all", label: "Redistribute everything remaining" },
];

export function defaultStrategy(course) {
  return course?.settings?.preserve_future_lectures === false ? "redistribute_all" : "preserve_future";
}
