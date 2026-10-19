/**
 * API client for the Adaptive Syllabus backend.
 *
 * Every response error is normalised into an ApiError carrying the backend's
 * error envelope ({code, message, details}) so views can show the professor
 * exactly what the server said.
 */

const TOKEN_KEY = "adaptive-syllabus.token";
const USER_KEY = "adaptive-syllabus.user";
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, { code = "ERROR", status = 0, details = {} } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details || {};
  }
}

export const session = {
  get token() {
    return localStorage.getItem(TOKEN_KEY);
  },
  get user() {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  },
  save(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
};

function buildQuery(query) {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

async function request(method, path, { body, query, form } = {}) {
  const headers = {};
  if (session.token) headers.Authorization = `Bearer ${session.token}`;

  let payload;
  if (form) {
    payload = form; // FormData sets its own content type.
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(`${BASE}${path}${buildQuery(query)}`, {
      method,
      headers,
      body: payload,
    });
  } catch (cause) {
    throw new ApiError("The server could not be reached. Check your connection.", {
      code: "NETWORK_ERROR",
      status: 0,
      details: { cause: String(cause) },
    });
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }

  if (!response.ok) {
    const envelope = data && data.error ? data.error : {};
    throw new ApiError(envelope.message || `Request failed (${response.status})`, {
      code: envelope.code || `HTTP_${response.status}`,
      status: response.status,
      details: envelope.details,
    });
  }
  return data;
}

const get = (path, query) => request("GET", path, { query });
const post = (path, body, query) => request("POST", path, { body, query });
const patch = (path, body) => request("PATCH", path, { body });
const put = (path, body) => request("PUT", path, { body });
const del = (path) => request("DELETE", path);

export const api = {
  // ---------------------------------------------------------------- auth
  login: (email, password) => post("/auth/login", { email, password }),
  register: (payload) => post("/auth/register", payload),
  me: () => get("/auth/me"),

  // -------------------------------------------------------------- courses
  listCourses: (query) => get("/courses", query),
  createCourse: (payload) => post("/courses", payload),
  getCourse: (courseId) => get(`/courses/${courseId}`),
  updateCourse: (courseId, payload) => patch(`/courses/${courseId}`, payload),
  setAvailability: (courseId, rules) => put(`/courses/${courseId}/availability`, rules),
  addException: (courseId, payload) => post(`/courses/${courseId}/exceptions`, payload),
  deleteException: (courseId, exceptionId) => del(`/courses/${courseId}/exceptions/${exceptionId}`),

  // --------------------------------------------------------------- syllabus
  listDocuments: (courseId) => get(`/courses/${courseId}/syllabus`),
  uploadSyllabus: (courseId, file) => {
    const form = new FormData();
    form.append("file", file);
    return request("POST", `/courses/${courseId}/syllabus/upload`, { form });
  },
  extractionPreview: (courseId, documentId) =>
    get(`/courses/${courseId}/syllabus/documents/${documentId}/preview`),
  confirmSyllabus: (courseId, payload) => post(`/courses/${courseId}/syllabus/confirm`, payload),
  syllabusStructure: (courseId) => get(`/courses/${courseId}/syllabus/structure`),
  updateTopic: (courseId, topicId, payload) =>
    patch(`/courses/${courseId}/syllabus/topics/${topicId}`, payload),
  deleteTopic: (courseId, topicId) => del(`/courses/${courseId}/syllabus/topics/${topicId}`),

  // --------------------------------------------------------------- schedule
  getSchedule: (courseId, revisionId) =>
    get(`/courses/${courseId}/schedule`, revisionId ? { revision_id: revisionId } : undefined),
  generateSchedule: (courseId, payload) => post(`/courses/${courseId}/schedule/generate`, payload),
  recalculate: (courseId, payload) => post(`/courses/${courseId}/schedule/recalculate`, payload),
  listRevisions: (courseId, query) => get(`/courses/${courseId}/schedule/revisions`, query),
  getRevision: (courseId, revisionId) =>
    get(`/courses/${courseId}/schedule/revisions/${revisionId}`),
  revisionDiff: (courseId, revisionId) =>
    get(`/courses/${courseId}/schedule/revisions/${revisionId}/diff`),
  revisionChanges: (courseId, revisionId) =>
    get(`/courses/${courseId}/schedule/revisions/${revisionId}/changes`),
  approveRevision: (courseId, revisionId, payload) =>
    post(`/courses/${courseId}/schedule/revisions/${revisionId}/approve`, payload),
  rejectRevision: (courseId, revisionId, payload) =>
    post(`/courses/${courseId}/schedule/revisions/${revisionId}/reject`, payload),
  rollbackRevision: (courseId, revisionId, payload) =>
    post(`/courses/${courseId}/schedule/revisions/${revisionId}/rollback`, payload),
  scheduleHistory: (courseId, query) => get(`/courses/${courseId}/schedule/history`, query),

  // --------------------------------------------------------------- lectures
  listLectures: (courseId, query) => get(`/courses/${courseId}/lectures`, query),
  getLecture: (courseId, lectureId) => get(`/courses/${courseId}/lectures/${lectureId}`),
  setLectureStatus: (courseId, lectureId, payload) =>
    patch(`/courses/${courseId}/lectures/${lectureId}/status`, payload),
  calendarEntries: (courseId, rangeStart, rangeEnd) =>
    get(`/courses/${courseId}/lectures/calendar/entries`, {
      range_start: rangeStart,
      range_end: rangeEnd,
    }),

  // ------------------------------------------------------- progress / meta
  dashboard: (courseId) => get(`/courses/${courseId}/dashboard`),
  progress: (courseId) => get(`/courses/${courseId}/progress`),
  calendar: (courseId, query) => get(`/courses/${courseId}/calendar`, query),

  // --------------------------------------------------------- notifications
  listNotifications: (query) => get("/notifications", query),
  markNotification: (id, read) => patch(`/notifications/${id}`, { read }),
  markAllNotifications: () => post("/notifications/read-all"),

  health: () => fetch("/health").then((response) => response.json()),
};
