import { BackgroundPaths } from "@/components/ui/background-paths";
import { Nav } from "@/components/nav";
import { SignalSection } from "@/components/sections/signal";
import { PatternSection } from "@/components/sections/pattern";
import { OpportunitySection } from "@/components/sections/opportunity";
import { PlatformSection } from "@/components/sections/platform";
import { ContactSection } from "@/components/sections/contact";

export default function Home() {
  return (
    <main className="bg-black">
      <Nav />
      <BackgroundPaths title="Baseload" />
      <SignalSection />
      <PatternSection />
      <OpportunitySection />
      <PlatformSection />
      <ContactSection />
    </main>
  );
}
