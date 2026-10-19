//+------------------------------------------------------------------+
//| goldbot_money_flow.mq5                                           |
//| Money flow line (tick-flow index) and its signals.               |
//|                                                                  |
//| Mirrors goldbot/features/smc_replica.py money_flow (n = 12 on    |
//| tick counts; the first bar adds to neither side) and             |
//| goldbot/features/mfi_signals.py mfi_signals: regime (60/40),     |
//| extreme circle (cross over 80 / under 20), double circle,        |
//| cross 50, pullback hold and pivot divergence (pivot_len 5).      |
//| Input defaults = the Python constants (ReplicaParams.mfi_*,      |
//| mfi_signals.BULL/BEAR/MID/OVERBOUGHT/OVERSOLD);                  |
//| tests/test_mql5_ports.py pins them.                              |
//|                                                                  |
//| No repaint: only closed bars are evaluated (the forming bar is   |
//| always EMPTY); a divergence is stamped on the bar that confirms  |
//| the pivot, never on the pivot bar. Volume = tick volume, as the  |
//| Python uses tick counts. Not compiled in CI (no MetaEditor).     |
//+------------------------------------------------------------------+
#property copyright "goldbot"
#property version   "1.00"
#property indicator_separate_window
#property indicator_minimum 0
#property indicator_maximum 100
#property indicator_level1  20
#property indicator_level2  40
#property indicator_level3  50
#property indicator_level4  60
#property indicator_level5  80
#property indicator_buffers 8
#property indicator_plots   8

#property indicator_label1  "Money flow"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrMediumPurple
#property indicator_width1  2
#property indicator_label2  "Extreme circle"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrOrange
#property indicator_label3  "Double circle"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrYellow
#property indicator_label4  "Regime"
#property indicator_type4   DRAW_NONE
#property indicator_label5  "Extreme circle side"
#property indicator_type5   DRAW_NONE
#property indicator_label6  "Double circle side"
#property indicator_type6   DRAW_NONE
#property indicator_label7  "Cross 50"
#property indicator_type7   DRAW_NONE
#property indicator_label8  "Pullback hold / divergence"
#property indicator_type8   DRAW_NONE

input int    MfiLen     = 12;     // money-flow period (ReplicaParams.mfi_len)
input double Overbought = 80.0;   // mfi_signals.OVERBOUGHT
input double Oversold   = 20.0;   // mfi_signals.OVERSOLD
input double BullLevel  = 60.0;   // mfi_signals.BULL
input double BearLevel  = 40.0;   // mfi_signals.BEAR
input double MidLevel   = 50.0;   // mfi_signals.MID
input int    PivotLen   = 5;      // mfi_signals(pivot_len=5): divergence pivots

#define NONE EMPTY_VALUE   // stands in for Python's NaN

double bMfi[], bCircleY[], bDoubleY[], bRegime[], bCircle[], bDouble[], bCross50[], bHoldDiv[];
int    g_next;
bool   wasOb, wasOs, bullS, bearS, dipped, bounced;
bool   hasPrevH, hasPrevL;
double prevHPrice, prevHLine, prevLPrice, prevLLine;

bool Valid(const double x) { return x != NONE; }

void ResetState()
  {
   g_next = 0; wasOb = false; wasOs = false; bullS = false; bearS = false; dipped = false; bounced = false;
   hasPrevH = false; hasPrevL = false; prevHPrice = 0; prevHLine = 0; prevLPrice = 0; prevLLine = 0;
  }

void Bind(const int k, double &buf[], const int arrow)
  {
   SetIndexBuffer(k, buf, INDICATOR_DATA);
   PlotIndexSetDouble(k, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   if(arrow > 0)
      PlotIndexSetInteger(k, PLOT_ARROW, arrow);
  }

int OnInit()
  {
   Bind(0, bMfi, 0); Bind(1, bCircleY, 159); Bind(2, bDoubleY, 108); Bind(3, bRegime, 0);
   Bind(4, bCircle, 0); Bind(5, bDouble, 0); Bind(6, bCross50, 0); Bind(7, bHoldDiv, 0);
   IndicatorSetString(INDICATOR_SHORTNAME, "goldbot money flow(" + IntegerToString(MfiLen) + ")");
   ResetState();
   return(INIT_SUCCEEDED);
  }

double Tp(const int i, const double &h[], const double &l[], const double &c[]) { return (h[i] + l[i] + c[i]) / 3.0; }

// smc_replica.money_flow at bar i: a window statistic of n bars, NONE when undefined.
double MoneyFlow(const int i, const double &h[], const double &l[], const double &c[], const long &v[])
  {
   if(i < MfiLen - 1)
      return NONE;
   double su = 0.0, sd = 0.0;
   for(int k = i - MfiLen + 1; k <= i; k++)
     {
      if(k == 0)
         continue;                                  // the first bar has no change
      double tp = Tp(k, h, l, c), ch = tp - Tp(k - 1, h, l, c);
      if(ch > 0) su += tp * (double)v[k];
      if(ch < 0) sd += tp * (double)v[k];
     }
   if(sd == 0.0)
      return (su > 0.0) ? 100.0 : NONE;              // zero downward flow: 100; no flow at all: NaN
   return 100.0 - 100.0 / (1.0 + su / sd);
  }

// smc_replica.pivot_points (classic): bar t-len is a pivot high (low), stamped at t.
bool IsPivot(const int t, const int len, const bool high, const double &h[], const double &l[])
  {
   if(t < 2 * len)
      return false;
   int c = t - len;
   for(int k = c - len; k <= t; k++)
     {
      if(k == c)
         continue;
      if(high && ((k < c && h[k] > h[c]) || (k > c && h[k] >= h[c])))
         return false;
      if(!high && ((k < c && l[k] < l[c]) || (k > c && l[k] <= l[c])))
         return false;
     }
   return true;
  }

void Step(const int i, const double &h[], const double &l[], const double &c[], const long &v[])
  {
   double m = MoneyFlow(i, h, l, c, v);
   bMfi[i] = m;
   double m1 = (i >= 1) ? bMfi[i - 1] : NONE, m2 = (i >= 2) ? bMfi[i - 2] : NONE;
   bCircleY[i] = NONE; bDoubleY[i] = NONE;

   // regime: +1 above BullLevel, -1 below BearLevel (NaN gives 0)
   bRegime[i] = !Valid(m) ? 0 : (m > BullLevel ? 1 : (m < BearLevel ? -1 : 0));

   // extreme circle and cross 50 (cross_over / cross_under; False where any value is NaN)
   double circle = 0, cross = 0;
   if(Valid(m) && Valid(m1))
     {
      if(m > Overbought && m1 <= Overbought) circle += 1;
      if(m < Oversold && m1 >= Oversold)     circle -= 1;
      if(m > MidLevel && m1 <= MidLevel)     cross += 1;
      if(m < MidLevel && m1 >= MidLevel)     cross -= 1;
     }
   bCircle[i] = circle; bCross50[i] = cross;
   if(circle != 0) bCircleY[i] = m;

   // double circle (mfi_signals._double_circle)
   double dbl = 0;
   if(i >= 1 && Valid(m) && Valid(m1))
     {
      if(m > Overbought) wasOb = true;
      else if(wasOb && m <= Overbought) { dbl = -1; wasOb = false; }
      if(m < Oversold) wasOs = true;
      else if(wasOs && m >= Oversold) { dbl = 1; wasOs = false; }
     }
   bDouble[i] = dbl;
   if(dbl != 0) bDoubleY[i] = m;

   // pullback hold (mfi_signals._pullback_hold)
   int hold = 0;
   if(i >= 2 && Valid(m) && Valid(m1) && Valid(m2))
     {
      if(m > BullLevel)      { bullS = true; dipped = false; }
      else if(m < BearLevel) bullS = false;
      else if(bullS)         dipped = true;
      if(m < BearLevel)      { bearS = true; bounced = false; }
      else if(m > BullLevel) bearS = false;
      else if(bearS)         bounced = true;
      double low = m1;
      if(bullS && dipped && low < BullLevel && low >= BearLevel && m2 > low && m > low)
        { hold = 1; dipped = false; }
      else if(bearS && bounced && low > BearLevel && low <= BullLevel && m2 < low && m < low)
        { hold = -1; bounced = false; }
     }

   // divergence (mfi_signals._divergence): the pivot bar j = i - PivotLen, known only now
   int div = 0;
   int j = i - PivotLen;
   if(j >= 0 && Valid(bMfi[j]))
     {
      if(IsPivot(i, PivotLen, true, h, l))
        {
         if(hasPrevH && h[j] > prevHPrice && bMfi[j] < prevHLine) div = -1;
         hasPrevH = true; prevHPrice = h[j]; prevHLine = bMfi[j];
        }
      if(IsPivot(i, PivotLen, false, h, l))
        {
         if(hasPrevL && l[j] < prevLPrice && bMfi[j] > prevLLine) div = 1;
         hasPrevL = true; prevLPrice = l[j]; prevLLine = bMfi[j];
        }
     }
   bHoldDiv[i] = hold * 10 + div;                    // tens: pullback hold, units: divergence
  }

void Blank(const int i)
  {
   bMfi[i] = NONE; bCircleY[i] = NONE; bDoubleY[i] = NONE; bRegime[i] = NONE;
   bCircle[i] = NONE; bDouble[i] = NONE; bCross50[i] = NONE; bHoldDiv[i] = NONE;
  }

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[],
                const double &high[], const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   if(prev_calculated == 0)
      ResetState();
   int closed = rates_total - 1;            // the forming bar is never evaluated (no repaint)
   for(int i = g_next; i < closed; i++)
      Step(i, high, low, close, tick_volume);
   if(closed > g_next)
      g_next = closed;
   if(rates_total > 0)
      Blank(rates_total - 1);
   return(rates_total);
  }
//+------------------------------------------------------------------+
