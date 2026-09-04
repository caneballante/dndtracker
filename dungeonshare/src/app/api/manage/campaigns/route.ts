import {
  accessErrorResponse,
  requireManager,
} from "@/lib/auth";
import { createCampaign, listCampaigns } from "@/lib/journal";
import { createCampaignSchema } from "@/lib/validation";

export async function GET() {
  try {
    await requireManager();
    return Response.json({ ok: true, campaigns: await listCampaigns() });
  } catch (error) {
    return (
      accessErrorResponse(error) ??
      Response.json(
        { ok: false, error: "Unable to load campaigns." },
        { status: 500 },
      )
    );
  }
}

export async function POST(request: Request) {
  try {
    await requireManager();
    const input = createCampaignSchema.parse(await request.json());
    const campaign = await createCampaign(input);
    return Response.json({ ok: true, campaign }, { status: 201 });
  } catch (error) {
    const accessResponse = accessErrorResponse(error);
    if (accessResponse) return accessResponse;
    const message =
      error instanceof Error ? error.message : "Unable to create campaign.";
    return Response.json({ ok: false, error: message }, { status: 400 });
  }
}
