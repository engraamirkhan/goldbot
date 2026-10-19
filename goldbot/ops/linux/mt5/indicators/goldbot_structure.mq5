//+------------------------------------------------------------------+
//| goldbot_structure.mq5                                            |
//| Market structure overlay: swing/internal BOS and CHoCH, equal    |
//| highs/lows, fair value gaps and the 50-61.8% golden zone.        |
//|                                                                  |
//| Mirrors goldbot/features/smc_replica.py: _leg_run / leg_pivots,  |
//| _structure_run (structure_breaks), pivot_points, _equal_run      |
//| (equal_levels), _fvg_run (fair_value_gaps), range_retracement.   |
//| Input defaults = ReplicaParams() (the owner's chart settings);   |
//| tests/test_mql5_ports.py pins them to the Python constants.      |
//|                                                                  |
//| No repaint: only closed bars are evaluated (the forming bar,     |
//| index rates_total-1, is always EMPTY), and every event is        |
//| stamped on the bar whose close makes it known, as in Python.     |
//| Arrays are indexed oldest first (index 0 = first bar), like the  |
//| Python arrays. Not compiled in CI (no MetaEditor available).     |
//+------------------------------------------------------------------+
#property copyright "goldbot"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 16
#property indicator_plots   16

#property indicator_label1  "Swing top"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrTomato
#property indicator_label2  "Swing bottom"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrDodgerBlue
#property indicator_label3  "Golden zone 50%"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrGold
#property indicator_style3  STYLE_DOT
#property indicator_label4  "Golden zone 61.8%"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrGoldenrod
#property indicator_style4  STYLE_DOT
#property indicator_label5  "Swing break up"
#property indicator_type5   DRAW_ARROW
#property indicator_color5  clrLime
#property indicator_label6  "Swing break down"
#property indicator_type6   DRAW_ARROW
#property indicator_color6  clrRed
#property indicator_label7  "Internal break up"
#property indicator_type7   DRAW_ARROW
#property indicator_color7  clrMediumSeaGreen
#property indicator_label8  "Internal break down"
#property indicator_type8   DRAW_ARROW
#property indicator_color8  clrIndianRed
#property indicator_label9  "Equal highs"
#property indicator_type9   DRAW_ARROW
#property indicator_color9  clrOrange
#property indicator_label10 "Equal lows"
#property indicator_type10  DRAW_ARROW
#property indicator_color10 clrAqua
#property indicator_label11 "FVG bull"
#property indicator_type11  DRAW_ARROW
#property indicator_color11 clrGreen
#property indicator_label12 "FVG bear"
#property indicator_type12  DRAW_ARROW
#property indicator_color12 clrMaroon
#property indicator_label13 "Trend swing"
#property indicator_type13  DRAW_NONE
#property indicator_label14 "Trend internal"
#property indicator_type14  DRAW_NONE
#property indicator_label15 "Swing event"
#property indicator_type15  DRAW_NONE
#property indicator_label16 "Internal event"
#property indicator_type16  DRAW_NONE

input int    SwingLen             = 50;     // leg-pivot length, swing structure
input int    InternalLen          = 5;      // leg-pivot length, internal structure
input bool   InternalCandleFilter = true;   // the asymmetric wick test on internal breaks
input int    EqualLen             = 3;      // classic pivot length for equal highs/lows
input double EqualThreshold       = 0.1;    // x ATR(EqualAtrLen)
input int    EqualAtrLen          = 200;
input bool   FvgAutoThreshold     = true;
input int    RangeLen             = 50;     // retracement range window
input double RangeFrac1           = 0.5;
input double RangeFrac2           = 0.618;

#define NONE EMPTY_VALUE   // stands in for Python's NaN

double bTop[], bBtm[], bG1[], bG2[], bUpS[], bDnS[], bUpI[], bDnI[], bEqh[], bEql[], bFvgU[], bFvgD[];
double bTrS[], bTrI[], bEvS[], bEvI[];

// resumable state (smc_replica.StructureState plus the equal-level, ATR and FVG carries)
int    g_next;
int    legS, legI;                         // -1 unknown (NaN), 0 down-leg, 1 up-leg
double ty, by, ity, iby, pty, pby, pity, piby, cp;
bool   tc, bc, itc, ibc;
int    ts, ti;
double eqLastH, eqLastL;
double atrAcc;
int    atrCnt;
double fvgCum;

bool Valid(const double x) { return x != NONE; }
bool Gt(const double a, const double b) { return Valid(a) && Valid(b) && a > b; }
bool Lt(const double a, const double b) { return Valid(a) && Valid(b) && a < b; }
bool Ge(const double a, const double b) { return Valid(a) && Valid(b) && a >= b; }
bool Le(const double a, const double b) { return Valid(a) && Valid(b) && a <= b; }
bool Ne(const double a, const double b) { return !(Valid(a) && Valid(b)) || a != b; }   // NaN != x is true

void ResetState()
  {
   g_next = 0; legS = -1; legI = -1;
   ty = NONE; by = NONE; ity = NONE; iby = NONE; pty = NONE; pby = NONE; pity = NONE; piby = NONE; cp = NONE;
   tc = false; bc = false; itc = false; ibc = false; ts = 0; ti = 0;
   eqLastH = NONE; eqLastL = NONE; atrAcc = 0.0; atrCnt = 0; fvgCum = 0.0;
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
   Bind(0, bTop, 0);   Bind(1, bBtm, 0);   Bind(2, bG1, 0);    Bind(3, bG2, 0);
   Bind(4, bUpS, 233); Bind(5, bDnS, 234); Bind(6, bUpI, 225); Bind(7, bDnI, 226);
   Bind(8, bEqh, 159); Bind(9, bEql, 159); Bind(10, bFvgU, 241); Bind(11, bFvgD, 242);
   Bind(12, bTrS, 0);  Bind(13, bTrI, 0);  Bind(14, bEvS, 0);  Bind(15, bEvI, 0);
   IndicatorSetString(INDICATOR_SHORTNAME, "goldbot structure");
   ResetState();
   return(INIT_SUCCEEDED);
  }

// smc_replica._leg_run at bar j: the new leg state (unchanged when neither side breaks).
int LegNext(const int j, const int length, const int leg, const double &h[], const double &l[])
  {
   if(j < length)
      return leg;
   double hh = h[j], ll = l[j];
   for(int k = j - length + 1; k <= j; k++) { hh = MathMax(hh, h[k]); ll = MathMin(ll, l[k]); }
   if(h[j - length] > hh)
      return 0;
   if(l[j - length] < ll)
      return 1;
   return leg;
  }

// smc_replica.pivot_points: bar t-length is a pivot high (low), stamped at t; NONE otherwise.
double ClassicPivot(const int t, const int length, const bool high, const double &h[], const double &l[])
  {
   if(t < 2 * length)
      return NONE;
   int c = t - length;
   for(int k = c - length; k <= t; k++)
     {
      if(k == c)
         continue;
      if(high && ((k < c && h[k] > h[c]) || (k > c && h[k] >= h[c])))
         return NONE;
      if(!high && ((k < c && l[k] < l[c]) || (k > c && l[k] <= l[c])))
         return NONE;
     }
   return high ? h[c] : l[c];
  }

void Step(const int i, const double &o[], const double &h[], const double &l[], const double &c[])
  {
   // ---- structure (smc_replica._structure_run); the first leg seen only sets the state
   int nS = LegNext(i, SwingLen, legS, h, l), nI = LegNext(i, InternalLen, legI, h, l);
   if(legS >= 0 && nS == 0 && legS != 0) { ty = h[i - SwingLen];    tc = true; }
   if(legS >= 0 && nS == 1 && legS != 1) { by = l[i - SwingLen];    bc = true; }
   if(legI >= 0 && nI == 0 && legI != 0) { ity = h[i - InternalLen]; itc = true; }
   if(legI >= 0 && nI == 1 && legI != 1) { iby = l[i - InternalLen]; ibc = true; }
   legS = nS; legI = nI;
   // rounded to 1e-9: a tie split by ~1e-14 counts as a tie (as fitted)
   double uw  = NormalizeDouble(h[i] - MathMax(c[i], o[i]), 9);
   double ref = NormalizeDouble(MathMin(c[i], o[i] - l[i]), 9);
   bool bullOk = InternalCandleFilter ? uw > ref : true;
   bool bearOk = InternalCandleFilter ? uw < ref : true;
   double ci = c[i];
   int evS = 0, evI = 0;
   bUpS[i] = NONE; bDnS[i] = NONE; bUpI[i] = NONE; bDnI[i] = NONE;
   if(itc && Gt(ci, ity) && Le(cp, pity) && Ne(ty, ity) && bullOk)
     { evI = (ti < 0) ? 2 : 1; itc = false; ti = 1; bUpI[i] = l[i]; }
   if(tc && Gt(ci, ty) && Le(cp, pty))
     { evS = (ts < 0) ? 2 : 1; tc = false; ts = 1; bUpS[i] = l[i]; }
   if(ibc && Lt(ci, iby) && Ge(cp, piby) && Ne(by, iby) && bearOk)
     { evI = (ti > 0) ? -2 : -1; ibc = false; ti = -1; bDnI[i] = h[i]; }
   if(bc && Lt(ci, by) && Ge(cp, pby))
     { evS = (ts > 0) ? -2 : -1; bc = false; ts = -1; bDnS[i] = h[i]; }
   bTrS[i] = ts; bTrI[i] = ti; bEvS[i] = evS; bEvI[i] = evI;   // event: +-1 BOS, +-2 CHoCH
   bTop[i] = ty; bBtm[i] = by;
   pty = ty; pby = by; pity = ity; piby = iby; cp = ci;

   // ---- equal highs / lows (smc_replica._equal_run): Wilder ATR, SMA-seeded, first TR = high - low
   double tr = (i == 0) ? h[0] - l[0]
               : MathMax(MathMax(h[i] - l[i], MathAbs(h[i] - c[i - 1])), MathAbs(l[i] - c[i - 1]));
   double a = NONE;
   double alpha = 1.0 / EqualAtrLen;
   atrCnt++;
   if(atrCnt < EqualAtrLen)
      atrAcc += tr;
   else if(atrCnt == EqualAtrLen)
     { atrAcc = (atrAcc + tr) / EqualAtrLen; a = atrAcc; }
   else
     { atrAcc = alpha * tr + (1.0 - alpha) * atrAcc; a = atrAcc; }
   bEqh[i] = NONE; bEql[i] = NONE;
   double ph = ClassicPivot(i, EqualLen, true, h, l);
   double pl = ClassicPivot(i, EqualLen, false, h, l);
   if(Valid(ph))
     {
      if(Valid(eqLastH) && Valid(a) && MathAbs(ph - eqLastH) < EqualThreshold * a)
         bEqh[i] = h[i];
      eqLastH = ph;
     }
   if(Valid(pl))
     {
      if(Valid(eqLastL) && Valid(a) && MathAbs(pl - eqLastL) < EqualThreshold * a)
         bEql[i] = l[i];
      eqLastL = pl;
     }

   // ---- fair value gaps (smc_replica._fvg_run): auto threshold = 2 x running mean |middle-bar body %|
   bFvgU[i] = NONE; bFvgD[i] = NONE;
   if(i >= 1)
     {
      double body = (c[i - 1] - o[i - 1]) / o[i - 1] * 100.0;
      fvgCum += MathAbs(body);
      double thr = FvgAutoThreshold ? fvgCum / i * 2.0 : 0.0;
      if(i >= 2)
        {
         if(l[i] > h[i - 2] && c[i - 1] > h[i - 2] && body > thr)
            bFvgU[i] = l[i];
         if(h[i] < l[i - 2] && c[i - 1] < l[i - 2] && -body > thr)
            bFvgD[i] = h[i];
        }
     }

   // ---- range retracement (smc_replica.range_retracement: the golden zone)
   bG1[i] = NONE; bG2[i] = NONE;
   if(i >= RangeLen - 1)
     {
      double hh = h[i], ll = l[i];
      for(int k = i - RangeLen + 1; k <= i; k++) { hh = MathMax(hh, h[k]); ll = MathMin(ll, l[k]); }
      bG1[i] = hh - RangeFrac1 * (hh - ll);
      bG2[i] = hh - RangeFrac2 * (hh - ll);
     }
  }

void Blank(const int i)
  {
   bTop[i] = NONE; bBtm[i] = NONE; bG1[i] = NONE; bG2[i] = NONE; bUpS[i] = NONE; bDnS[i] = NONE;
   bUpI[i] = NONE; bDnI[i] = NONE; bEqh[i] = NONE; bEql[i] = NONE; bFvgU[i] = NONE; bFvgD[i] = NONE;
   bTrS[i] = NONE; bTrI[i] = NONE; bEvS[i] = NONE; bEvI[i] = NONE;
  }

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[],
                const double &high[], const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   if(prev_calculated == 0)
      ResetState();
   int closed = rates_total - 1;            // the forming bar is never evaluated (no repaint)
   for(int i = g_next; i < closed; i++)
      Step(i, open, high, low, close);
   if(closed > g_next)
      g_next = closed;
   if(rates_total > 0)
      Blank(rates_total - 1);
   return(rates_total);
  }
//+------------------------------------------------------------------+
