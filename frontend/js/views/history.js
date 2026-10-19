/** Revision history, change ledger, rollback, and the notification inbox. */

import { api } from "../api.js";
import {
  badge,
  bulletList,
  el,
  emptyState,
  errorState,
  formDialog,
  mount,
  notice,
  openModal,
  stat,
  table,
  toast,
} from "../dom.js";
import { dateLabel, dateTimeLabel, minutesLabel } from "../format.js";

const APPROVAL_BADGE = {
  draft: "",
  pending_approval: "warn",
  approved: "ok",
  rejected: "danger",
  superseded: "",
};

export async function render(ctx) {
  const courseId = ctx.course.id;
  const tz = ctx.timezone;

  const [revisionsPage, historyPage] = await Promise.all([
    api.listRevisions(courseId, { limit: 50 }),
    api.scheduleHistory(courseId, { limit: 50 }),
  ]);

  const revisions = revisionsPage.items;
  const current = revisions.find((revision) => revision.approval_status === "approved");
  const pending = revisions.find((revision) => revision.approval_status === "pending_approval");

  const restore = async (revision) => {
    const values = await formDialog({
      title: `Restore revision ${revision.revision_number}`,
      message:
        "The timetable is rebuilt from that revision (new lectures are added, later ones are replaced). The restore itself is recorded as a new revision, so nothing is lost.",
      fields: [{ name: "reason", label: "Reason", type: "textarea", hint: "Kept in the audit history." }],
      confirmLabel: "Restore this revision",
    });
    if (!values) return;
    try {
      await api.rollbackRevision(courseId, revision.id, { reason: values.reason || "" });
      toast(`Revision ${revision.revision_number} restored.`, "ok");
      await render(ctx);
    } catch (error) {
      toast(error.message, "error");
    }
  };

  const showDiff = async (revision) => {
    try {
      const diff = await api.revisionDiff(courseId, revision.id);
      const changes = diff.diff?.changes || [];
      openModal({
        title: `Revision ${revision.revision_number} · what changed`,
        body: el(
          "div",
          {},
          el("p", { text: diff.diff?.summary || revision.change_summary || "No summary recorded." }),
          table(
            [
              { label: "Change" },
              { label: "Topic" },
              { label: "Before" },
              { label: "After" },
              { label: "Why" },
            ],
            changes.map((change) => [
              badge(change.change_type.replace("_", " "), "accent"),
              change.topic_title || "—",
              change.previous_date
                ? `${dateLabel(change.previous_date, tz)} · ${minutesLabel(change.previous_minutes ?? 0)}`
                : "—",
              change.new_date
                ? `${dateLabel(change.new_date, tz)} · ${minutesLabel(change.new_minutes ?? 0)}`
                : "—",
              change.change_reason,
            ]),
            { empty: "No lecture changed in this revision." },
          ),
        ),
        actions: [{ label: "Close", kind: "secondary" }],
      });
    } catch (error) {
      toast(error.message, "error");
    }
  };

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "History" }),
        el("p", {
          text: "Every approved timetable is kept. Nothing is overwritten and any revision can be restored.",
        }),
      ),
      el("button", {
        class: "btn btn--secondary",
        type: "button",
        text: "Open the timetable",
        onClick: () => ctx.navigate(`#/courses/${courseId}/schedule`),
      }),
    ),

    pending
      ? notice(
          "warn",
          `Revision ${pending.revision_number} is waiting for approval`,
          el("p", { text: pending.change_summary || "A proposed change needs your decision." }),
          el("button", {
            class: "btn btn--small",
            type: "button",
            text: "Review and decide",
            onClick: () => ctx.navigate(`#/courses/${courseId}/schedule`),
          }),
        )
      : null,

    el(
      "div",
      { class: "grid grid--stats" },
      stat({ label: "Revisions", value: revisionsPage.meta.total }),
      stat({ label: "Current", value: current ? `#${current.revision_number}` : "—", kind: current ? "ok" : "warn" }),
      stat({ label: "Changes recorded", value: historyPage.meta.total }),
      stat({
        label: "Last change",
        value: revisions[0] ? dateLabel(revisions[0].created_at, tz) : "—",
        hint: revisions[0] ? revisions[0].trigger_type.replaceAll("_", " ") : "",
      }),
    ),

    el(
      "div",
      { class: "card" },
      el("div", { class: "card__head" }, el("h2", { text: "Revisions" })),
      table(
        [
          { label: "#", numeric: true },
          { label: "Status" },
          { label: "Trigger" },
          { label: "Summary" },
          { label: "Created" },
          { label: "Approved" },
          { label: "" },
        ],
        revisions.map((revision) => [
          String(revision.revision_number),
          badge(revision.approval_status.replace("_", " "), APPROVAL_BADGE[revision.approval_status] ?? ""),
          revision.trigger_type.replaceAll("_", " "),
          el(
            "div",
            {},
            el("div", { text: revision.change_summary || "—" }),
            revision.warnings?.length
              ? el("div", { class: "muted", text: `${revision.warnings.length} planner note(s)` })
              : null,
          ),
          dateTimeLabel(revision.created_at, tz),
          revision.approved_at ? dateTimeLabel(revision.approved_at, tz) : "—",
          el(
            "div",
            { class: "row-actions" },
            el("button", {
              class: "btn btn--secondary btn--small",
              type: "button",
              text: "Changes",
              onClick: () => showDiff(revision),
            }),
            revision.approval_status === "pending_approval"
              ? el("button", {
                  class: "btn btn--small",
                  type: "button",
                  text: "Decide",
                  onClick: () => ctx.navigate(`#/courses/${courseId}/schedule`),
                })
              : null,
            ["approved", "superseded"].includes(revision.approval_status) && revision.id !== current?.id
              ? el("button", {
                  class: "btn btn--ghost btn--small",
                  type: "button",
                  text: "Restore",
                  onClick: () => restore(revision),
                })
              : null,
          ),
        ]),
        { empty: "No revisions yet." },
      ),
    ),

    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card__head" },
        el("h2", { text: "Change ledger" }),
        el("span", { class: "muted", text: "What moved, when, and why" }),
      ),
      table(
        [
          { label: "When" },
          { label: "Revision", numeric: true },
          { label: "Trigger" },
          { label: "What happened" },
          { label: "Changes", numeric: true },
        ],
        historyPage.items.map((entry) => [
          el(
            "div",
            {},
            el("div", { text: dateTimeLabel(entry.created_at, tz) }),
            entry.published_at
              ? el("div", { class: "muted", text: `published ${dateLabel(entry.published_at, tz)}` })
              : el("div", { class: "muted", text: "not published" }),
          ),
          String(entry.revision_number),
          entry.trigger_type.replaceAll("_", " "),
          el(
            "div",
            {},
            el("div", { text: entry.change_summary || "—" }),
            entry.warnings?.length ? bulletList(entry.warnings) : null,
          ),
          String(entry.change_count),
        ]),
        { empty: "No schedule changes recorded yet." },
      ),
    ),
  );
}

export async function renderNotifications(ctx) {
  const data = await api.listNotifications({ limit: 100 });

  const reload = () => renderNotifications(ctx);

  mount(
    ctx.root,
    el(
      "div",
      { class: "page-head" },
      el(
        "div",
        {},
        el("h1", { text: "Notifications" }),
        el("p", {
          text: "Sent after a schedule change was approved. Nothing is announced before you approve it (unless automatic publishing is enabled).",
        }),
      ),
      el("button", {
        class: "btn btn--secondary",
        type: "button",
        text: "Mark all as read",
        onClick: async () => {
          await api.markAllNotifications();
          toast("All notifications marked as read.", "ok");
          ctx.refreshNotificationBadge();
          await reload();
        },
      }),
    ),

    el(
      "div",
      { class: "grid grid--stats" },
      stat({ label: "Unread", value: data.unread_count, kind: data.unread_count ? "warn" : "ok" }),
      stat({ label: "Total shown", value: data.items.length }),
    ),

    data.items.length
      ? el(
          "div",
          { class: "card" },
          el(
            "ul",
            { class: "timeline" },
            data.items.map((item) =>
              el(
                "li",
                { class: `timeline__item${item.read_at ? "" : " timeline__item--current"}` },
                el(
                  "div",
                  { class: "row row--between" },
                  el("strong", { text: item.message }),
                  badge(item.read_at ? "read" : "new", item.read_at ? "" : "warn"),
                ),
                el("div", {
                  class: "timeline__meta",
                  text: `${item.event_type.replaceAll("_", " ")} · ${dateTimeLabel(item.created_at)}`,
                }),
                el(
                  "div",
                  { class: "row", style: { marginTop: "6px" } },
                  item.course_id
                    ? el("a", {
                        href: `#/courses/${item.course_id}/history`,
                        text: "Open course history",
                      })
                    : null,
                  !item.read_at
                    ? el("button", {
                        class: "btn btn--ghost btn--small",
                        type: "button",
                        text: "Mark as read",
                        onClick: async () => {
                          await api.markNotification(item.id, true);
                          ctx.refreshNotificationBadge();
                          await reload();
                        },
                      })
                    : null,
                ),
              ),
            ),
          ),
        )
      : emptyState("No notifications yet."),
  );
}
