"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";

export function ContactSection() {
  const [submitted, setSubmitted] = useState(false);
  const [form, setForm] = useState({ name: "", email: "", message: "" });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Placeholder — wire to a backend or Formspree later
    setSubmitted(true);
  };

  return (
    <section id="contact" className="bg-black py-32 px-6">
      <div className="max-w-2xl mx-auto">
        <div className="mb-12">
          <p className="text-white/40 text-sm uppercase tracking-widest mb-3">05 — Contact</p>
          <h2 className="text-4xl md:text-5xl font-bold text-white tracking-tight mb-4">
            Let's talk.
          </h2>
          <p className="text-white/50 text-lg">
            Interested in the analysis, the data, or what a deployment looks like?
            Reach out and we'll respond within 24 hours.
          </p>
        </div>

        {submitted ? (
          <div className="border border-white/10 rounded-xl p-10 text-center">
            <p className="text-white text-xl font-semibold mb-2">Message received.</p>
            <p className="text-white/40">We'll be in touch shortly.</p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <div>
              <label className="block text-white/40 text-xs uppercase tracking-widest mb-2">Name</label>
              <input
                type="text"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white text-sm placeholder-white/20 focus:outline-none focus:border-white/30 transition-colors"
                placeholder="Your name"
              />
            </div>
            <div>
              <label className="block text-white/40 text-xs uppercase tracking-widest mb-2">Email</label>
              <input
                type="email"
                required
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white text-sm placeholder-white/20 focus:outline-none focus:border-white/30 transition-colors"
                placeholder="you@fund.com"
              />
            </div>
            <div>
              <label className="block text-white/40 text-xs uppercase tracking-widest mb-2">Message</label>
              <textarea
                required
                rows={5}
                value={form.message}
                onChange={(e) => setForm({ ...form, message: e.target.value })}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white text-sm placeholder-white/20 focus:outline-none focus:border-white/30 transition-colors resize-none"
                placeholder="What are you working on?"
              />
            </div>
            <Button
              type="submit"
              className="w-full bg-white text-black hover:bg-white/90 font-semibold py-6 rounded-xl text-base transition-colors"
            >
              Send Message →
            </Button>
          </form>
        )}

        <p className="text-white/20 text-xs text-center mt-10">
          © {new Date().getFullYear()} Baseload. All rights reserved.
        </p>
      </div>
    </section>
  );
}
