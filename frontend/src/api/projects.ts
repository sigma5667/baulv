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
