/**
 * Application shell: hash router, navigation, session handling, notifications.
 *
 * Views are ES modules that render into #view. No build step and no framework:
 * the API is the single source of truth and every view re-fetches on entry.
 */

import { ApiError, api, session } from "./api.js";
import { clear, el, errorState, openModal, mount, toast } from "./dom.js";
import { dateTimeLabel } from "./format.js";

import * as authView from "./views/auth.js";
import * as coursesView from "./views/courses.js";
import * as dashboardView from "./views/dashboard.js";
import * as historyView from "./views/history.js";
import * as lecturesView from "./views/lectures.js";
import * as progressView from "./views/progress.js";
import * as scheduleView from "./views/schedule.js";
import * as settingsView from "./views/settings.js";
import * as syllabusView from "./views/syllabus.js";

const view = document.getElementById("view");
const sidebar = document.getElementById("sidebar");
const sidebarNav = document.getElementById("sidebar-nav");
const sidebarCourse = document.getElementById("sidebar-course");
const userChip = document.getElementById("user-chip");
const userName = document.getElementById("user-name");
const notificationBadge = document.getElementById("notifications-count");

const COURSE_SECTIONS = [
  { key: "", label: "Dashboard" },
  { key: "syllabus", label: "Syllabus" },
  { key: "schedule", label: "Schedule" },
  { key: "lectures", label: "Lectures" },
  { key: "progress", label: "Progress" },
  { key: "history", label: "History" },
  { key: "settings", label: "Settings" },
];

function parseRoute(hash) {
  const path = (hash || "").replace(/^#\/?/, "").split("?")[0];
  const segments = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (!segments.length) return { name: "courses", params: {} };
  if (segments[0] === "login") return { name: "login", params: {} };
  if (segments[0] === "register") return { name: "register", params: {} };
  if (segments[0] === "notifications") return { name: "notifications", params: {} };
  if (segments[0] === "courses") {
    if (segments[1] === "new") return { name: "course-new", params: {} };
    if (!segments[1]) return { name: "courses", params: {} };
    const params = { courseId: segments[1] };
    const section = segments[2] || "";
    if (section === "lectures" && segments[3]) params.lectureId = segments[3];
    return { name: `course-${section || "dashboard"}`, params };
  }
  return { name: "not-found", params: {} };
}

export function navigate(hash) {
  const target = hash.startsWith("#") ? hash : `#/${hash.replace(/^\/+/, "")}`;
  if (window.location.hash === target) {
    handleRoute();
    return;
  }
  window.location.hash = target;
}

function setActiveSection(courseId, section) {
  for (const link of sidebarNav.querySelectorAll("a")) {
    const isActive = link.dataset.section === section;
    if (isActive) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  sidebar.dataset.courseId = courseId || "";
}

function renderSidebar(course) {
  if (!course) {
    sidebar.hidden = true;
    clear(sidebarNav);
    clear(sidebarCourse);
    return;
  }
  sidebar.hidden = false;
  mount(
    sidebarCourse,
    el("strong", { text: course.course_code || course.name }),
    el("span", { text: course.name }),
  );
  mount(
    sidebarNav,
    COURSE_SECTIONS.map((section) => {
      const href = `#/courses/${course.id}${section.key ? `/${section.key}` : ""}`;
      return el(
        "li",
        {},
        el("a", { href, text: section.label, dataset: { section: section.key } }),
      );
    }),
  );
}

async function refreshNotificationBadge() {
  if (!session.token) {
    notificationBadge.hidden = true;
    return;
  }
  try {
    const data = await api.listNotifications({ unread_only: true, limit: 1 });
    const unread = data.unread_count || 0;
    notificationBadge.hidden = unread === 0;
    notificationBadge.textContent = unread > 99 ? "99+" : String(unread);
  } catch {
    notificationBadge.hidden = true;
  }
}

async function showNotifications() {
  const body = el("div", { class: "stack" }, el("div", { class: "loading-state", text: "Loading…" }));
  const dialog = openModal({
    title: "Notifications",
    body,
    actions: [
      {
        label: "Mark all as read",
        kind: "secondary",
        onClick: async () => {
          await api.markAllNotifications();
          toast("All notifications marked as read", "ok");
          refreshNotificationBadge();
        },
      },
    ],
  });

  const load = async () => {
    const data = await api.listNotifications({ limit: 50 });
    if (!data.items.length) {
      mount(body, el("div", { class: "empty-state", text: "No notifications yet." }));
      return;
    }
    mount(
      body,
      data.items.map((item) =>
        el(
          "div",
          { class: `notice notice--${item.read_at ? "info" : "warn"}` },
          el("div", { class: "notice__title", text: item.message }),
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
                  onClick: () => dialog.close(),
                })
              : null,
            !item.read_at
              ? el("button", {
                  class: "btn btn--ghost btn--small",
                  type: "button",
                  text: "Mark read",
                  onClick: async () => {
                    await api.markNotification(item.id, true);
                    refreshNotificationBadge();
                    await load();
                  },
                })
              : null,
          ),
        ),
      ),
    );
  };

  try {
    await load();
  } catch (error) {
    mount(body, errorState(error));
  }
}

async function renderCourseChrome(courseId) {
  try {
    const course = await api.getCourse(courseId);
    renderSidebar(course);
    return course;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      renderSidebar(null);
      toast("That course no longer exists.", "error");
      navigate("#/courses");
      return null;
    }
    throw error;
  }
}

async function handleRoute() {
  const route = parseRoute(window.location.hash);
  const requiresSession = !["login", "register"].includes(route.name);

  if (requiresSession && !session.token) {
    navigate("#/login");
    return;
  }

  userChip.hidden = !session.token;
  if (session.user) {
    mount(userName, session.user.name || session.user.email);
  }

  const ctx = {
    root: view,
    params: route.params,
    navigate,
    refreshNotificationBadge,
    user: session.user,
    course: null,
    timezone: "UTC",
  };

  clear(view);
  view.append(el("div", { class: "loading-state", text: "Loading…" }));

  try {
    switch (route.name) {
      case "login":
      case "register":
        renderSidebar(null);
        await authView.render(ctx, route.name);
        break;
      case "courses":
        renderSidebar(null);
        await coursesView.render(ctx);
        break;
      case "course-new":
        renderSidebar(null);
        await coursesView.renderWizard(ctx);
        break;
      case "notifications":
        renderSidebar(null);
        await historyView.renderNotifications(ctx);
        break;
      case "course-dashboard":
      case "course-syllabus":
      case "course-schedule":
      case "course-lectures":
      case "course-progress":
      case "course-history":
      case "course-settings": {
        const course = await renderCourseChrome(route.params.courseId);
        if (!course) return;
        ctx.course = course;
        ctx.timezone = course.timezone || "UTC";
        const section = route.name.replace("course-", "");
        setActiveSection(course.id, section === "dashboard" ? "" : section);
        if (route.name === "course-dashboard") await dashboardView.render(ctx);
        else if (route.name === "course-syllabus") await syllabusView.render(ctx);
        else if (route.name === "course-schedule") await scheduleView.render(ctx);
        else if (route.name === "course-lectures") {
          if (route.params.lectureId) await lecturesView.renderDetail(ctx);
          else await lecturesView.render(ctx);
        } else if (route.name === "course-progress") await progressView.render(ctx);
        else if (route.name === "course-history") await historyView.render(ctx);
        else if (route.name === "course-settings") await settingsView.render(ctx);
        break;
      }
      default:
        renderSidebar(null);
        mount(ctx.root, errorState({ message: "That page does not exist.", code: "NOT_FOUND" }));
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      session.clear();
      toast("Your session expired. Please sign in again.", "error");
      navigate("#/login");
      return;
    }
    mount(view, el("h1", { text: "Something went wrong" }), errorState(error));
    console.error(error);
  } finally {
    view.focus();
    refreshNotificationBadge();
  }
}

function boot() {
  document.getElementById("logout-button").addEventListener("click", () => {
    session.clear();
    renderSidebar(null);
    toast("Signed out.", "info");
    navigate("#/login");
  });
  document.getElementById("all-courses").addEventListener("click", () => navigate("#/courses"));
  document
    .getElementById("notifications-button")
    .addEventListener("click", () => showNotifications());

  window.addEventListener("hashchange", handleRoute);
  handleRoute();

  // Keep the unread badge fresh without hammering the API.
  window.setInterval(() => {
    if (session.token && !document.hidden) refreshNotificationBadge();
  }, 60000);
}

boot();
