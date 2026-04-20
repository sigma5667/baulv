import api from "./client";
import type { Room } from "../types/room";

export const fetchProjectRooms = async (projectId: string): Promise<Room[]> => {
  const { data } = await api.get(`/projects/${projectId}/rooms`);
  return data;
};

export const updateRoom = async (roomId: string, updates: Partial<Room>): Promise<Room> => {
  const { data } = await api.put(`/rooms/${roomId}`, updates);
  return data;
};

export const deleteRoom = async (roomId: string): Promise<void> => {
  await api.delete(`/rooms/${roomId}`);
};

// `name` is the only required field on RoomCreate (backend schema);
// all other fields are optional and default sensibly.
export const createRoom = async (
  unitId: string,
  data: Partial<Room> & { name: string }
): Promise<Room> => {
  const { data: res } = await api.post(`/units/${unitId}/rooms`, data);
  return res;
};
