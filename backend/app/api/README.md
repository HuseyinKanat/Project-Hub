# api/

REST routes only. **No business logic.** Each route delegates to a service
in `app/services/`. Routes are thin adapters.

Permission checks happen in services, not here.
