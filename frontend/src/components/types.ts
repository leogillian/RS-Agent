/**
 * Shared types for RS-Agent frontend components.
 */
import type { PayloadType, TraceStep } from "../api";

export type Role = "user" | "assistant";

export interface Message {
  id: number;
  role: Role;
  text: string;
  payloadType?: PayloadType;
  images?: string[];
  promptToUser?: string;
  traceSteps?: TraceStep[];
  streaming?: boolean;
  createdAt?: string;
}
