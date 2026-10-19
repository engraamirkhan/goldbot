//+------------------------------------------------------------------+
//| goldbot_sr_boxes.mq5                                             |
//| Support/resistance boxes, their break-and-close events, the next |
//| box above/below the close and the volume candle class.           |
//|                                                                  |
//| Mirrors goldbot/features/smc_replica.py: _channels_now,          |
//| _channel_run (sr_channel_breaks) and _candle_run (candle_class), |
//| plus goldbot/features/box_course.py next_box (the target is the  |
//| next box). Input defaults = ReplicaParams() sr_* and candle_*    |
//| (the owner's chart settings); tests/test_mql5_ports.py pins them.|
//|                                                                  |
//| No repaint: only closed bars are evaluated (the forming bar is   |
//| always EMPTY). Boxes are rebuilt only on the bar that confirms a |
//| classic pivot (pivot_len bars after it); a break is stamped on   |
//| its closing bar. The rectangles drawn are the boxes in force at  |
//| the last closed bar. Volume = tick volume (Python: tick counts). |
//| Not compiled in CI (no MetaEditor available).                    |
//+------------------------------------------------------------------+
#property copyright "goldbot"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 6
#property indicator_plots   6

#property indicator_label1  "Resistance break"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrLime
#property indicator_label2  "Support break"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrRed
#property indicator_label3  "Next box above"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrSeaGreen
#property indicator_style3  STYLE_DASH
#property indicator_label4  "Next box below"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrFireBrick
#property indicator_style4  STYLE_DASH
#property indicator_label5  "Candle class"
#property indicator_type5   DRAW_NONE
#property indicator_label6  "Boxes in force"
#property indicator_type6   DRAW_NONE

input int    SrPivotLen      = 5;      // classic pivot length of the box pivots
input double SrWidthPct      = 1.0;    // box width, % of the high-low range of the last SrRangeWindow bars
input int    SrMaxChannels   = 15;     // boxes kept
input int    SrLookback      = 300;    // bars of pivots/touches used to build the boxes
input int    SrRangeWindow   = 300;    // width is a % of the high-low range of the last N bars
input int    CandleVolLen    = 20;     // seeded EMA of the tick volume
input double CandleHighMult  = 1.5;    // high-volume candle: volume > mult x EMA
input double CandleLowMult   = 0.5;    // low-volume candle: volume < mult x EMA
input color  BoxColor        = clrSlateGray;

#define NONE EMPTY_VALUE
#define PREFIX "goldbot_box_"

double bUp[], bDn[], bAbove[], bBelow[], bCandle[], bCount[];
int    g_next;
double pv[];          // pivot values, oldest first (Python keeps them newest first)
int    loc[];         // their bar indices
double tops[], bots[];
double volAcc;
int    volCnt;

bool Valid(const double x) { return x != NONE; }

void ResetState()
  {
   g_next = 0; ArrayResize(pv, 0); ArrayResize(loc, 0); ArrayResize(tops, 0); ArrayResize(bots, 0);
   volAcc = 0.0; volCnt = 0;
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
   Bind(0, bUp, 233); Bind(1, bDn, 234); Bind(2, bAbove, 0); Bind(3, bBelow, 0); Bind(4, bCandle, 0); Bind(5, bCount, 0);
   IndicatorSetString(INDICATOR_SHORTNAME, "goldbot boxes");
   ResetState();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { ObjectsDeleteAll(0, PREFIX); }

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

// smc_replica._channels_now: the strongest non-overlapping channels, strongest first, from the pivots
// (newest first), touches over bars a0..i and the width cw (cwOk false = NaN width: nothing grows).
void ChannelsNow(const int a0, const int i, const double cw, const bool cwOk, const double &h[], const double &l[])
  {
   int m = ArraySize(pv);
   double p[], lo[], hi[], strength[];
   ArrayResize(p, m); ArrayResize(lo, m); ArrayResize(hi, m); ArrayResize(strength, m);
   for(int x = 0; x < m; x++) { p[x] = pv[m - 1 - x]; lo[x] = p[x]; hi[x] = p[x]; strength[x] = 0.0; }
   for(int y = 0; y < m; y++)
     {
      double cpp = p[y];
      for(int x = 0; x < m; x++)
        {
         bool below = cpp <= hi[x];
         double w = below ? hi[x] - cpp : cpp - lo[x];
         if(!cwOk || !(w <= cw))
            continue;
         if(below) lo[x] = MathMin(lo[x], cpp);
         else      hi[x] = MathMax(hi[x], cpp);
         strength[x] += 20.0;
        }
     }
   for(int x = 0; x < m; x++)
      for(int k = a0; k <= i; k++)
         if((h[k] <= hi[x] && h[k] >= lo[x]) || (l[k] <= hi[x] && l[k] >= lo[x]))
            strength[x] += 1.0;
   ArrayResize(tops, 0); ArrayResize(bots, 0);
   int keep = MathMin(m, SrMaxChannels);
   for(int r = 0; r < keep; r++)
     {
      int best = -1;
      for(int x = 0; x < m; x++)
         if(strength[x] >= 20.0 && (best < 0 || strength[x] > strength[best]))
            best = x;                                // first maximum = newest pivot among ties
      if(best < 0)
         break;
      double tk = hi[best], bk = lo[best];
      int n = ArraySize(tops);
      ArrayResize(tops, n + 1); ArrayResize(bots, n + 1);
      tops[n] = tk; bots[n] = bk;
      for(int x = 0; x < m; x++)
         if((hi[x] <= tk && hi[x] >= bk) || (lo[x] <= tk && lo[x] >= bk))
            strength[x] = -1.0;
     }
  }

void Step(const int i, const double &o[], const double &h[], const double &l[], const double &c[], const long &v[])
  {
   // ---- box book, rebuilt on a pivot's confirming bar (smc_replica._channel_run)
   bool hasH = IsPivot(i, SrPivotLen, true, h, l), hasL = IsPivot(i, SrPivotLen, false, h, l);
   if(hasH || hasL)
     {
      int n = ArraySize(pv);
      ArrayResize(pv, n + 1); ArrayResize(loc, n + 1);
      pv[n] = hasH ? h[i - SrPivotLen] : l[i - SrPivotLen];
      loc[n] = i;
      while(ArraySize(loc) > 0 && i - loc[0] > SrLookback)
        { ArrayRemove(pv, 0, 1); ArrayRemove(loc, 0, 1); }
      bool cwOk = i >= SrRangeWindow - 1;
      double cw = 0.0;
      if(cwOk)
        {
         double hh = h[i], ll = l[i];
         for(int k = i - SrRangeWindow + 1; k <= i; k++) { hh = MathMax(hh, h[k]); ll = MathMin(ll, l[k]); }
         cw = (hh - ll) * SrWidthPct / 100.0;
        }
      ChannelsNow(MathMax(0, i - SrLookback), i, cw, cwOk, h, l);
     }
   int nb = ArraySize(tops);
   bCount[i] = nb;

   // ---- break and close (resistance: previous close < top <= close; support as fitted)
   bUp[i] = NONE; bDn[i] = NONE;
   if(i > 0 && nb > 0)
     {
      double cp = c[i - 1], ci = c[i];
      bool up = false, dn = false;
      int hiIdx = 0, loIdx = 0;                         // highest channel first; the lowest channel last (stable)
      for(int k = 0; k < nb; k++)
        {
         if(cp < tops[k] && ci >= tops[k]) up = true;
         if(tops[k] > tops[hiIdx]) hiIdx = k;
         if(tops[k] <= tops[loIdx]) loIdx = k;
        }
      if(cp > tops[hiIdx] && tops[hiIdx] >= ci)
         dn = true;
      for(int k = 0; k < nb && !dn; k++)
         if(k != loIdx && cp > bots[k] && ci <= bots[k])
            dn = true;
      if(up) bUp[i] = l[i];
      if(dn) bDn[i] = h[i];
     }

   // ---- next box (box_course.next_box): long, lowest bottom above the close; short, highest top below it
   bAbove[i] = NONE; bBelow[i] = NONE;
   for(int k = 0; k < nb; k++)
     {
      if(bots[k] > c[i] && (!Valid(bAbove[i]) || bots[k] < bAbove[i])) bAbove[i] = bots[k];
      if(tops[k] < c[i] && (!Valid(bBelow[i]) || tops[k] > bBelow[i])) bBelow[i] = tops[k];
     }

   // ---- candle class (smc_replica._candle_run; traverse off): 4 engulf, 1/2 high-volume bull/bear, 3 low, 0
   double vol = (double)v[i], ve = NONE, alpha = 2.0 / (CandleVolLen + 1);
   volCnt++;
   if(volCnt < CandleVolLen)       volAcc += vol;
   else if(volCnt == CandleVolLen) { volAcc = (volAcc + vol) / CandleVolLen; ve = volAcc; }
   else                            { volAcc = alpha * vol + (1.0 - alpha) * volAcc; ve = volAcc; }
   int code = 0;
   bool engulf = i > 0 && (((c[i] > o[i]) && (c[i - 1] < o[i - 1]) && (c[i] > o[i - 1]) && (o[i] < c[i - 1]))
                           || ((c[i] < o[i]) && (c[i - 1] > o[i - 1]) && (c[i] < o[i - 1]) && (o[i] > c[i - 1])));
   bool hiV = Valid(ve) && vol > CandleHighMult * ve, loV = Valid(ve) && vol < CandleLowMult * ve;
   if(engulf)                    code = 4;
   else if(hiV && c[i] > o[i])   code = 1;
   else if(hiV && c[i] < o[i])   code = 2;
   else if(loV)                  code = 3;
   bCandle[i] = code;
  }

void DrawBoxes(const int last, const datetime &time[], const int rates_total)
  {
   ObjectsDeleteAll(0, PREFIX);
   datetime t0 = time[MathMax(0, last - SrLookback)], t1 = time[rates_total - 1];
   for(int k = 0; k < ArraySize(tops); k++)
     {
      string name = PREFIX + IntegerToString(k);
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t0, tops[k], t1, bots[k]);
      ObjectSetInteger(0, name, OBJPROP_COLOR, BoxColor);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
     }
  }

void Blank(const int i)
  {
   bUp[i] = NONE; bDn[i] = NONE; bAbove[i] = NONE; bBelow[i] = NONE; bCandle[i] = NONE; bCount[i] = NONE;
  }

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[],
                const double &high[], const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   if(prev_calculated == 0)
      ResetState();
   int closed = rates_total - 1;            // the forming bar is never evaluated (no repaint)
   bool stepped = false;
   for(int i = g_next; i < closed; i++)
     {
      Step(i, open, high, low, close, tick_volume);
      stepped = true;
     }
   if(closed > g_next)
      g_next = closed;
   if(rates_total > 0)
      Blank(rates_total - 1);
   if(stepped)
      DrawBoxes(closed - 1, time, rates_total);
   return(rates_total);
  }
//+------------------------------------------------------------------+
