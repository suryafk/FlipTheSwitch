import { apiGet } from "./client";

/** Mirrors backend/app/slack/bridge.py health(). Contains no secrets. */
export interface SlackStatus {
  status: string; // healthy | idle | degraded | disconnected | not_configured | blocked | error
  connected: boolean;
  workspace: { team_id: string; team_name: string } | null;
  deactivated_reason: string | null;
  scopes: string[];
  channel_id: string | null;
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  backlog: number;
  deliveries: Record<string, number>;
  oauth_configured: boolean;
  commands_enabled: boolean;
}

export function getSlackStatus(signal?: AbortSignal): Promise<SlackStatus> {
  return apiGet<SlackStatus>("/api/slack/status", { signal });
}

export const SLACK_INSTALL_PATH = "/api/slack/install";
