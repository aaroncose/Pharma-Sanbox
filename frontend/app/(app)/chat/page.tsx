import { ChatClient } from "./ChatClient";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  const denied = await guard("chat.use");
  if (denied) return denied;

  return <ChatClient />;
}
