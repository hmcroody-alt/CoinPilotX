# PulseSoc Intelligence Operating System

Date: 2026-06-27

## Summary

The Dashboard Intelligence Center now uses a backend-managed operating model instead of generic cards. The internal design standard remains invisible in product UI; users see PulseSoc Intelligence, Intelligence Hub, Pulse Brain, AI Advisor, Safety Scan, and other public-facing subsystem labels.

## User-Facing Intelligence Hub

The new Intelligence Hub summarizes:

- Overall Intelligence Score
- Platform Health
- Safety Score
- Active Threats
- Current Scam Alerts
- AI Recommendations
- Trending Topics
- Community Mood
- Risk Score
- Trust Score
- Prediction Confidence
- Security Events
- New Opportunities
- Creator Insights
- Personalized Daily Brief

The hub is generated server-side from privacy-safe aggregate and user-owned signals. Missing tables safely resolve to zero/ready states instead of exposing errors or fake private data.

## Subsystems

Implemented backend-managed subsystems:

- Scam Shield: Protection Center
- Scam Alerts: Alert Center
- Pulse Brain: Open Pulse Brain
- AI Advisor: Ask AI Advisor
- Safety Scan: Scan My Account
- Smart Recommendations: Explore Recommendations
- Security Intelligence: Review Security
- Threat Intelligence: Analyze Threats
- Risk Assessment: Assess Risk
- Trust Intelligence: Review Trust
- Signal Intelligence: Analyze Signals
- Research Workspace: Start Research
- Feed Intelligence: View Feed Intelligence
- Prediction Center: View Predictions
- Pulse Heatmap: Explore Heatmaps

Each subsystem exposes intelligence, prediction, automation, safety, explainability, confidence scoring, backend route, audit posture, and cross-module event propagation notes.

## Cross-Module Event Mesh

The Intelligence service defines event propagation patterns so one risk signal updates related systems:

- Scam detected updates Scam Shield, Alert Center, Threat Intelligence, Security Intelligence, Notifications, and Network Security.
- High-risk login updates Risk Assessment, Security Operations, Trust Intelligence, Account Security, and the Intelligence Hub.
- Trending topics update Pulse Brain, Feed Intelligence, Smart Recommendations, Creator Studio, and Pulse Heatmap.
- Copyright or moderation signals update Trust Intelligence, Creator Reputation, Content Performance, Monetization, and the Intelligence Hub.
- Marketplace risk updates Risk Assessment, Scam Shield, Marketplace, Notifications, and Admin Review.

## Security Boundary

- No private messages are exposed.
- No raw push tokens or secrets are exposed.
- User dashboard data is scoped to the authenticated user.
- Admin surfaces require admin permission checks.
- Dynamic text is escaped before rendering.
- Missing data falls back safely without pretending unsupported features are fully live.

## Files

- `services/dashboard_intelligence_command_center.py`
- `services/pulse_dashboard_mission_control.py`
- `services/backend_management_registry.py`
- `bot.py`
- `scripts/intelligence_loginexus_operating_system_audit.py`

## Current Scope

This mission establishes the complete Intelligence operating surface, dashboard state builder, contextual buttons, admin command center surfaces, and audit coverage. Provider-specific AI execution remains gated by existing AI configuration and does not auto-act without permission.
