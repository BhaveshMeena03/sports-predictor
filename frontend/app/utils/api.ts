// Single source of truth for the backend location.
// Local dev hits the local FastAPI; deployed builds point at Fly via
// NEXT_PUBLIC_API_BASE (set in Vercel project env).
export const API =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api";
