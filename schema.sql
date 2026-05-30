-- ============================================================
--  DCF Valuation Database Schema
--  Dialect: Microsoft SQL Server (T-SQL)
--  Tables:
--    1. TickerInput        — all inputs + generated date
--    2. TickerCalculation  — summary output, FK -> TickerInput
--    3. TickerKeyComments  — analyst comments, FK -> TickerInput
-- ============================================================

-- ---------------------------------------------------------------
-- 1. TickerInput
-- ---------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'TickerInput')
BEGIN
CREATE TABLE dbo.TickerInput (
    id                  INT             IDENTITY(1,1)   PRIMARY KEY,

    -- Identity
    ticker              NVARCHAR(20)    NOT NULL,
    company_name        NVARCHAR(255)   NULL,
    exchange            NVARCHAR(50)    NULL,
    currency_unit       NVARCHAR(30)    NULL,           -- e.g. "USD mm" / "INR Crore"

    -- Market data inputs
    stock_price         DECIMAL(14,4)   NOT NULL,
    shares_outstanding  DECIMAL(14,4)   NULL,           -- in millions
    net_debt            DECIMAL(18,4)   NULL,           -- negative = net cash
    wacc                DECIMAL(8,6)    NULL,           -- e.g. 0.1200
    base_revenue        DECIMAL(18,4)   NULL,
    base_year           SMALLINT        NULL,           -- e.g. 2025

    -- DCF projection assumptions
    growth_yr1_2        DECIMAL(8,6)    NULL,
    growth_yr3_5        DECIMAL(8,6)    NULL,
    terminal_growth     DECIMAL(8,6)    NULL,
    ebitda_margin_yr1   DECIMAL(8,6)    NULL,
    ebitda_margin_yr5   DECIMAL(8,6)    NULL,
    dna_pct_rev         DECIMAL(8,6)    NULL,
    capex_pct_rev       DECIMAL(8,6)    NULL,
    nwc_pct_rev         DECIMAL(8,6)    NULL,
    tax_rate            DECIMAL(8,6)    NULL,

    -- Growth model inputs
    base_eps            DECIMAL(14,4)   NULL,
    eps_growth_yr1_5    DECIMAL(8,6)    NULL,
    eps_growth_yr6_10   DECIMAL(8,6)    NULL,
    terminal_pe         DECIMAL(8,2)    NULL,
    required_return     DECIMAL(8,6)    NULL,
    dividend_per_share  DECIMAL(10,4)   NULL,

    -- Historical data snapshot (JSON string)
    -- Format: [{"yr":"FY2021","rev":1234.5,"margin":0.12}, ...]
    historical_data     NVARCHAR(MAX)   NULL,           -- store as JSON string

    -- Metadata
    generated_at        DATETIME2       NOT NULL DEFAULT GETUTCDATE(),
    created_by          NVARCHAR(100)   NULL,
    notes               NVARCHAR(MAX)   NULL,

    CONSTRAINT chk_ticker_upper CHECK (ticker = UPPER(ticker))
);
END
GO

CREATE INDEX IF NOT EXISTS idx_ticker_input_ticker
    ON dbo.TickerInput (ticker);
GO
CREATE INDEX idx_ticker_input_generated
    ON dbo.TickerInput (generated_at DESC);
GO
CREATE INDEX idx_ticker_input_ticker_date
    ON dbo.TickerInput (ticker, generated_at DESC);
GO


-- ---------------------------------------------------------------
-- 2. TickerCalculation
-- ---------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'TickerCalculation')
BEGIN
CREATE TABLE dbo.TickerCalculation (
    id                  INT             IDENTITY(1,1)   PRIMARY KEY,
    input_id            INT             NOT NULL
                            REFERENCES dbo.TickerInput(id) ON DELETE CASCADE,

    -- Which model
    calc_type           NVARCHAR(10)    NOT NULL
                            CHECK (calc_type IN ('DCF','GROWTH')),

    -- Core outputs
    intrinsic_value     DECIMAL(14,4)   NOT NULL,
    current_price       DECIMAL(14,4)   NOT NULL,
    upside_pct          DECIMAL(8,4)    NULL,           -- (IV-price)/price*100

    -- MOS buy prices
    mos_10_price        DECIMAL(14,4)   NULL,
    mos_20_price        DECIMAL(14,4)   NULL,
    mos_25_price        DECIMAL(14,4)   NULL,
    mos_30_price        DECIMAL(14,4)   NULL,

    -- DCF-specific (NULL for Growth rows)
    pv_fcf_sum          DECIMAL(18,4)   NULL,
    pv_terminal_value   DECIMAL(18,4)   NULL,
    enterprise_value    DECIMAL(18,4)   NULL,
    equity_value        DECIMAL(18,4)   NULL,

    -- Growth-specific (NULL for DCF rows)
    eps_year10          DECIMAL(14,4)   NULL,
    terminal_price      DECIMAL(14,4)   NULL,
    pv_terminal_price   DECIMAL(14,4)   NULL,
    pv_dividends_sum    DECIMAL(14,4)   NULL,

    -- Blended
    blended_fair_value  DECIMAL(14,4)   NULL,

    -- Verdict (computed column)
    verdict             AS (
                            CASE
                                WHEN upside_pct >= 30  THEN 'STRONG BUY'
                                WHEN upside_pct >= 10  THEN 'BUY'
                                WHEN upside_pct >= -10 THEN 'HOLD'
                                WHEN upside_pct >= -25 THEN 'SELL'
                                ELSE 'STRONG SELL'
                            END
                        ) PERSISTED,

    calculated_at       DATETIME2       NOT NULL DEFAULT GETUTCDATE()
);
END
GO

CREATE INDEX idx_calc_input_id
    ON dbo.TickerCalculation (input_id);
GO
CREATE INDEX idx_calc_type
    ON dbo.TickerCalculation (calc_type);
GO
CREATE INDEX idx_calc_verdict
    ON dbo.TickerCalculation (verdict);
GO


-- ---------------------------------------------------------------
-- 3. TickerKeyComments
-- ---------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'TickerKeyComments')
BEGIN
CREATE TABLE dbo.TickerKeyComments (
    id                  INT             IDENTITY(1,1)   PRIMARY KEY,
    input_id            INT             NOT NULL
                            REFERENCES dbo.TickerInput(id) ON DELETE CASCADE,

    comment_type        NVARCHAR(30)    NOT NULL
                            CHECK (comment_type IN (
                                'RATIONALE',
                                'RISK_HIGH',
                                'RISK_MED',
                                'RISK_LOW',
                                'STRESS_BULL',
                                'STRESS_BEAR',
                                'BALANCE',
                                'ANALYST'
                            )),

    title               NVARCHAR(255)   NULL,
    body                NVARCHAR(MAX)   NOT NULL,
    sort_order          SMALLINT        DEFAULT 0,

    source              NVARCHAR(20)    DEFAULT 'AI'
                            CHECK (source IN ('AI','MANUAL','IMPORTED')),

    created_at          DATETIME2       NOT NULL DEFAULT GETUTCDATE()
);
END
GO

CREATE INDEX idx_comments_input_id
    ON dbo.TickerKeyComments (input_id);
GO
CREATE INDEX idx_comments_type
    ON dbo.TickerKeyComments (input_id, comment_type);
GO


-- ---------------------------------------------------------------
-- View: latest valuation per ticker
-- ---------------------------------------------------------------
GO
CREATE OR ALTER VIEW dbo.v_latest_valuations AS
WITH latest AS (
    SELECT id, ticker, company_name, stock_price, currency_unit, generated_at,
           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY generated_at DESC) AS rn
    FROM dbo.TickerInput
)
SELECT
    l.ticker,
    l.company_name,
    l.stock_price,
    l.currency_unit,
    l.generated_at,
    dcf.intrinsic_value         AS dcf_iv,
    dcf.upside_pct              AS dcf_upside_pct,
    dcf.verdict                 AS dcf_verdict,
    grw.intrinsic_value         AS growth_iv,
    grw.upside_pct              AS growth_upside_pct,
    grw.verdict                 AS growth_verdict,
    ROUND(
        (ISNULL(dcf.intrinsic_value,0) + ISNULL(grw.intrinsic_value,0))
        / NULLIF(
            CASE WHEN dcf.id IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN grw.id IS NOT NULL THEN 1 ELSE 0 END, 0
          ), 4
    )                           AS blended_iv
FROM latest l
LEFT JOIN dbo.TickerCalculation dcf
    ON dcf.input_id = l.id AND dcf.calc_type = 'DCF'
LEFT JOIN dbo.TickerCalculation grw
    ON grw.input_id = l.id AND grw.calc_type = 'GROWTH'
WHERE l.rn = 1;
GO


-- ---------------------------------------------------------------
-- Sample queries
-- ---------------------------------------------------------------

-- Latest summary for a ticker:
-- SELECT * FROM dbo.v_latest_valuations WHERE ticker = 'TSLA';

-- Full history for a ticker:
-- SELECT ti.generated_at, tc.calc_type, tc.intrinsic_value, tc.upside_pct, tc.verdict
-- FROM dbo.TickerInput ti
-- JOIN dbo.TickerCalculation tc ON tc.input_id = ti.id
-- WHERE ti.ticker = 'TSLA'
-- ORDER BY ti.generated_at DESC;

-- All comments for a valuation run:
-- SELECT comment_type, title, body
-- FROM dbo.TickerKeyComments
-- WHERE input_id = 1
-- ORDER BY comment_type, sort_order;
