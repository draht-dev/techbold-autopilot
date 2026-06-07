# The Agent That Works a Ticket

Imagine you're a service-desk technician. A ticket comes in: something's broken on a customer's machine. You open the ERP, read the ticket, SSH into the box, poke around, figure out what went wrong, fix it, and then — the part nobody loves — you write up what you did and close the ticket.
We wanted to build an assistant that does this with you. Not for you. With you. 

## One agent, not a pipeline

Here's the first thing you might do differently. 
When you hear "diagnose, then fix, then report," your instinct is to build three things. A diagnoser. A fixer. A reporter. Hand the output of one to the next.

But think about what actually happens in the handoff. The diagnoser saw the raw logs, watched the failed units, noticed that the disk was almost full but not quite. By the time that reaches the fixer, it's been flattened into "probably a disk issue." You threw away the context. And context is the entire job.

So we did the boring thing instead: one agent. It owns the whole investigation from the SSH connection to the final report. Everything it sees — the recon output, every command it ran, which hypothesis you picked, the edit you made to its fix plan — all of it lives in one continuous conversation.

If you've used Cursor's debug mode, this will feel familiar, because that's where we stole it from. A single agent that decides its own next command, rather than marching through a fixed sequence of stages.

There's a quieter benefit too. One agent is just easier to follow. There's a single train of thought you can read and steer. No mental model of which model is talking to which.

## The loop

It's not a rigid pipeline (we just covered why), but in practice a run moves like this:

1. It connects to the VM over SSH — you approve the connection first. It runs a fixed set of read-only commands to get its bearings: OS, failed units, logs, disk, memory, sockets, processes.
2. It tries to reproduce the problem you were told about.
3. Then it proposes two or more ranked guesses at the root cause.
4. Now it stops. You pick one. Or you write your own. Or you drop a comment to nudge it.
5. The agent investigates whatever you chose, and if it turns out the root cause was wrong, it comes back and proposes a fresh set.
6. When it's confident, it hands you one reviewable plan: here's the explanation, here are the commands, here's how I'll validate it, here's how I'll roll it back.
7. You approve, edit, or reject.
8. It runs the fix, collects proof that it worked, and drafts the activity report.
9. You rework that, submit it, and the ERP closes the ticket.

Notice how many times it stopped to ask you something. That's not an accident.

## The LLM is never the security boundary

We implemented a deterministic safety layer (safety/rules.py) that classifies every command before it can run, and it's enforced at the tool layer and again at the SSH runner.

### Commands fall into four buckets:

- **Allow** — a whitelist of read-only commands. Auto-approved, if you've turned on "auto-approve safe reads."
- **Deny** — destructive operations are hard-blocked and cannot be approved. Not by the agent, not by you. Recursive rm of system paths, dropping databases, turning the firewall off, reading key or secret files, log tampering, fork bombs. The button isn't disabled; the action genuinely cannot happen.
- **Confirm** — anything that mutates state, or anything the layer doesn't recognize, pauses for you. Approve, edit, or reject with a reason.
- **Redact** — command output gets scrubbed of secrets (keys, tokens, passwords, connection strings) before it's shown to you, the AI, logged, or written into a report.

And the human oversight isn't a setting you can forget to enable. It's woven through the loop: you approve the connection, pick the hypothesis, review and edit the fix plan, answer the agent when it's stuck, sign off the report. And there's a STOP button that aborts the whole thing, any time, for any reason.

## You never leave the screen

The fastest way to ruin this is to make the technician juggle four apps. So they don't.
Everything is in one screen, SAP-style. The ticket list, the ticket detail, the live terminal, the approval prompts, the hypothesis picker, the report review — all the same app. No separate SSH client. No separate logs tool. No tab to the ERP's web UI. Approvals, the shell, and the report submission all happen inline, right where you already are.

## The terminal is a real terminal

The embedded terminal is xterm.js with the fit addon, backed by a real PTY with stdin enabled. Which means full-screen programs actually work — vim, htop, less, the whole lot. A ResizeObserver plus the fit addon re-fit the terminal whenever the panel, tab, or window changes size, and the new cols/rows get pushed down to the backend PTY. So it stays correct no matter how you arrange your layout. And if you want to ignore the agent entirely and just drop into a plain SSH session in that same terminal — you can.

## It looks boring on purpose

The design is deliberately plain. Neutral, dense, functional — oriented at SAP Fiori. Color shows up only to mean something: status (open / pending / done) and priority. Nothing decorative is competing for your attention.
This was a choice, not a shortcut. The point is a tool a technician can read at a glance and steer quickly. Not a dashboard that looks great in a screenshot and slows you down every day after