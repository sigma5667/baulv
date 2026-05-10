import api from "./client";
import type { Project, ProjectCreate } from "../types/project";

export const fetchProjects = async (): Promise<Project[]> => {
  const { data } = await api.get("/projects");
  return data;
};

export const fetchProject = async (id: string): Promise<Project> => {
  const { data } = await api.get(`/projects/${id}`);
  return data;
};

export const createProject = async (project: ProjectCreate): Promise<Project> => {
  const { data } = await api.post("/projects", project);
  return data;
};

export const updateProject = async (id: string, project: Partial<ProjectCreate>): Promise<Project> => {
  const { data } = await api.put(`/projects/${id}`, project);
  return data;
};

export const deleteProject = async (id: string): Promise<void> => {
  await api.delete(`/projects/${id}`);
};

/**
 * v23.9 — Download a printable A4 Mengenermittlung PDF for the
 * project. Streams the response as a Blob and triggers a native
 * browser download. Available to every authenticated owner of the
 * project — no Pro-gate.
 *
 * Filename is derived from ``Content-Disposition``; falls back to
 * a project-id-based name if the header is missing (e.g. mock
 * environments). Any 4xx/5xx is rethrown so the calling mutation
 * can surface a German error toast.
 */
export const downloadMengenermittlungPdf = async (
  projectId: string,
): Promise<void> => {
  const res = await api.get(
    `/projects/${projectId}/mengenermittlung.pdf`,
    { responseType: "blob" },
  );

  // Pull the server-suggested filename out of Content-Disposition
  // when present — that way the user sees ``Mengenermittlung_<name>.pdf``
  // instead of a UUID. Fallback keeps the download functional.
  const disposition = res.headers["content-disposition"] as
    | string
    | undefined;
  let filename = `Mengenermittlung_${projectId}.pdf`;
  if (disposition) {
    const match = /filename="?([^";]+)"?/i.exec(disposition);
    if (match) filename = match[1];
  }

  const blob = new Blob([res.data], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};
