# Design

Diagrams are written in [Mermaid](https://mermaid.js.org/), which GitHub renders directly.

## 1. Architecture

```mermaid
flowchart LR
    B[Browser<br/>warden or resident] -->|HTTP, session cookie| A
    subgraph Flask app
      A[app.py<br/>routes, roles, CSRF, templates] --> H[hostel.py<br/>SQL and transactions]
      H --> R[rules.py<br/>pure functions]
      A --> R
    end
    H --> D[(SQLite<br/>schema.sql)]
```

`rules.py` imports nothing from the other layers, so every rule is testable without a database.

## 2. Data flow, level 0

```mermaid
flowchart LR
    W([Warden]) -- rooms, admissions, payments, remarks, notices --> S((Hostel and PG<br/>Management System))
    S -- room map, bills, receipts, reports --> W
    Rs([Resident]) -- login, complaints --> S
    S -- room, dues, receipts, notices --> Rs
```

## 3. Data flow, level 1

```mermaid
flowchart TB
    W([Warden]) --> P1((1.0 Manage rooms))
    W --> P2((2.0 Admit, move, check out))
    W --> P3((3.0 Generate bills))
    W --> P4((4.0 Record payments))
    W --> P5((5.0 Handle complaints and notices))
    Rs([Resident]) --> P6((6.0 Resident portal))
    P1 <--> D1[(D1 rooms)]
    P2 <--> D2[(D2 residents and users)]
    P3 <--> D3[(D3 invoices)]
    P4 <--> D4[(D4 payments)]
    P5 <--> D5[(D5 complaints and notices)]
    P6 --> D3 & D4 & D5
```

## 4. Entity relationship diagram

```mermaid
erDiagram
    ROOMS ||--o{ RESIDENTS : "houses (active)"
    RESIDENTS ||--o| USERS : "has login"
    RESIDENTS ||--o{ INVOICES : "is billed"
    INVOICES ||--o{ PAYMENTS : "is paid by"
    RESIDENTS ||--o{ COMPLAINTS : raises
    ROOMS { int id PK  text number UK  text block  int floor  int capacity  int rent  int ac }
    RESIDENTS { int id PK  text name  text gender  text phone UK  text email UK  int room_id FK  text joined_on  text left_on  int deposit  text status }
    USERS { int id PK  text email UK  text password_hash  text role  int resident_id FK }
    INVOICES { int id PK  int resident_id FK  text month  text room_number  int rent  int late_fee  text due_on }
    PAYMENTS { int id PK  int invoice_id FK  int amount  text mode  text paid_on  text receipt_no UK }
    COMPLAINTS { int id PK  int resident_id FK  text category  text description  text status  text remark }
    NOTICES { int id PK  text title  text body  text posted_on }
```

`UNIQUE (resident_id, month)` on `invoices` is what makes "never bill twice" a database guarantee.
`invoice_summary` is a view that adds `total`, `paid` and `balance` to each invoice.

## 5. Flowchart: admitting a resident

```mermaid
flowchart TD
    A[Warden submits form] --> B{Valid name, phone,<br/>email, date?}
    B -- no --> X[Show message]
    B -- yes --> C[BEGIN IMMEDIATE]
    C --> D{Room in the gender's block<br/>and a bed free?}
    D -- no --> R[ROLLBACK, show message]
    D -- yes --> E[INSERT resident and user login]
    E --> F{Duplicate phone or email?}
    F -- yes --> R
    F -- no --> G[COMMIT, show login once]
```

## 6. Flowchart: recording a payment

```mermaid
flowchart TD
    A[Amount and mode submitted] --> B[BEGIN IMMEDIATE]
    B --> C[Add late fee if bill is past due and unpaid]
    C --> D[balance = rent + late fee - paid]
    D --> E{balance > 0 and<br/>1 <= amount <= balance?}
    E -- no --> R[ROLLBACK, show message]
    E -- yes --> F[INSERT payment, set receipt RCP-year-id]
    F --> G[COMMIT, show receipt]
```
