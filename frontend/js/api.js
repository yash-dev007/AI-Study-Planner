/**
 * API client for the Adaptive Syllabus backend.
 *
 * Every response error is normalised into an ApiError carrying the backend's
 * error envelope ({code, message, details}) so views can show the professor
 * exactly what the server said.
 */

const USER_KEY = "adaptive-syllabus.user";
const BASE = "/api/v1";

// Sessions live in the HttpOnly cookie set by the backend. Older builds kept a
// bearer token in localStorage; drop it so it cannot linger after this upgrade.
localStorage.removeItem("adaptive-syllabus.token");

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
  get user() {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  },
  save(user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear() {
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
      credentials: "same-origin",
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
    const error = new ApiError(envelope.message || `Request failed (${response.status})`, {
      code: envelope.code || `HTTP_${response.status}`,
      status: response.status,
      details: envelope.details,
    });
    if (response.status === 401 && !path.startsWith("/auth/")) {
      // Let the shell end the session once, however deep in the UI the call was.
      window.dispatchEvent(new CustomEvent("auth:unauthorized", { detail: error }));
    }
    throw error;
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
  logout: () => post("/auth/logout"),
  me: () => get("/auth/me"),

  // -------------------------------------------------------------- courses
  listCourses: (query) => get("/courses", query),
  createCourse: (payload) => post("/courses", payload),
  getCourse: (courseId) => get(`/courses/${courseId}`),
  updateCourse: (courseId, payload) => patch(`/courses/${courseId}`, payload),
  deleteCourse: (courseId) => del(`/courses/${courseId}`),
  setAvailability: (courseId, rules) => put(`/courses/${courseId}/availability`, rules),
  addException: (courseId, payload) => post(`/courses/${courseId}/exceptions`, payload),
  deleteException: (courseId, exceptionId) => del(`/courses/${courseId}/exceptions/${exceptionId}`),

  // --------------------------------------------------------------- syllabus
  listDocuments: (courseId) => get(`/courses/${courseId}/syllabus`),
  uploadSyllabus: (courseId, file, onProgress) => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/courses/${courseId}/syllabus/upload`);
      xhr.withCredentials = true;
      if (xhr.upload && typeof onProgress === "function") {
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && event.total > 0) {
            const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
            onProgress({ loaded: event.loaded, total: event.total, percent });
          }
        };
      }
      xhr.onload = () => {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch {
          data = { raw: xhr.responseText };
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
        } else {
          const envelope = data && data.error ? data.error : {};
          reject(
            new ApiError(envelope.message || `Request failed (${xhr.status})`, {
              code: envelope.code || `HTTP_${xhr.status}`,
              status: xhr.status,
              details: envelope.details || {},
            }),
          );
        }
      };
      xhr.onerror = () => {
        reject(
          new ApiError("The server could not be reached. Check your connection.", {
            code: "NETWORK_ERROR",
            status: 0,
          }),
        );
      };
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  },
  uploadTextSyllabus: (courseId, payload) => post(`/courses/${courseId}/syllabus/text`, payload),
  extractionPreview: (courseId, documentId) =>
    get(`/courses/${courseId}/syllabus/documents/${documentId}/preview`),
  refinePreview: (courseId, documentId, payload) =>
    post(`/courses/${courseId}/syllabus/documents/${documentId}/refine`, payload),
  confirmSyllabus: (courseId, payload) => post(`/courses/${courseId}/syllabus/confirm`, payload),
  syllabusStructure: (courseId) => get(`/courses/${courseId}/syllabus/structure`),
  saveSyllabusStructure: (courseId, payload) =>
    put(`/courses/${courseId}/syllabus`, payload),
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

  // ------------------------------------------------------------- settings
  getSettings: () => get("/settings"),
  updateSettings: (payload) => put("/settings", payload),
  saveAiConfig: (payload) => post("/settings/ai-config", payload),
  testAiConnection: (payload) => post("/settings/ai-test", payload),

  health: () => fetch("/health").then((response) => response.json()),
};
