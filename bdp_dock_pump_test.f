C ======================================================================
C  RV2900WD Dock BDP Pump Test Tool  --  FORTRAN 77 edition
C  ---------------------------------------------------------------------
C  Port of bdp_dock_pump_test.py.  F77 has no GUI, no C interop and no
C  serial library, so this is a console REPL that talks to a COM port the
C  way PC Fortran did historically: by OPENing the port as a file.
C  Configure the port FIRST from the OS, e.g. on DOS/Windows:
C      MODE COM5: BAUD=115200 PARITY=n DATA=8 STOP=1
C  then run this program and type:  open COM5
C
C  The BDP command catalog and the ? / * / $ protocol are preserved.
C
C  Build (gfortran):
C      gfortran -std=legacy -o bdp bdp_dock_pump_test.f
C  Run:
C      ./bdp        (type `help`)
C ======================================================================
      PROGRAM BDP
      IMPLICIT NONE
      INTEGER MAXT, LU
      PARAMETER (MAXT=38, LU=10)

      CHARACTER*56 TNAME(MAXT)
      CHARACTER*10 TQRY(MAXT), TWRT(MAXT), TSTP(MAXT)
      CHARACTER*8  TPDEF(MAXT)

      CHARACTER*200 LINE, REST, TAIL
      CHARACTER*56  FIRST
      CHARACTER*32  NUMT, PARM, PORT
      CHARACTER*64  CMD
      INTEGER NT, IDX, IOS, I, NEOL
      LOGICAL CONN

      CHARACTER*2 EOLCH
      COMMON /CFGI/ NEOL
      COMMON /CFGC/ EOLCH

C     ----- shared MIDI state (see the MIDI subroutines at end of file) -----
      INTEGER MAXB, MAXE
      PARAMETER (MAXB=20000, MAXE=8000)
      INTEGER NB, MB(MAXB)
      COMMON /MBUF/ NB, MB
      INTEGER DIVS, NE, ET(MAXE), EK(MAXE), E0(MAXE), E1(MAXE),
     &        E2(MAXE), ENB(MAXE), ETP(MAXE)
      COMMON /MEVT/ DIVS, NE, ET, EK, E0, E1, E2, ENB, ETP
      LOGICAL MLOADED, OKM

      INTEGER LENTRM
      EXTERNAL LENTRM

      NT = MAXT
      CONN = .FALSE.
      MLOADED = .FALSE.
      NEOL = 1
      EOLCH(1:1) = CHAR(13)
      EOLCH(2:2) = ' '

      CALL FILLCT(TNAME, TQRY, TWRT, TSTP, TPDEF, MAXT)

      WRITE(*,'(1X,A)') 'RV2900WD Dock BDP Pump Test Tool (FORTRAN 77)'
      WRITE(*,'(1X,A)') 'BDP Command Spec V2 -- COM port as a file.'
      WRITE(*,'(1X,A)') 'Type help for commands.'

C     ---------------- main REPL loop ----------------
100   CONTINUE
      WRITE(*,'(1X,A,$)') 'bdp> '
      READ(*,'(A)',END=900,ERR=900) LINE
      CALL SPLTOK(LINE, FIRST, REST)
      CALL TOLOW(FIRST)
      IF (LENTRM(FIRST) .EQ. 0) GOTO 100

      IF (FIRST .EQ. 'help' .OR. FIRST .EQ. '?') THEN
        CALL HELP
      ELSE IF (FIRST .EQ. 'quit' .OR. FIRST .EQ. 'exit') THEN
        GOTO 900
      ELSE IF (FIRST .EQ. 'ports') THEN
        WRITE(*,'(1X,A)') 'COM ports come from the OS (F77 cannot'
        WRITE(*,'(1X,A)') 'enumerate them).  On Windows try COM1..COMn'
        WRITE(*,'(1X,A)') 'and configure with the MODE command.'
      ELSE IF (FIRST .EQ. 'open') THEN
        CALL SPLTOK(REST, PORT, TAIL)
        IF (LENTRM(PORT) .EQ. 0) THEN
          WRITE(*,'(1X,A)') 'Usage:  open COMx'
        ELSE
          OPEN(UNIT=LU, FILE=PORT(1:LENTRM(PORT)), STATUS='OLD',
     &         IOSTAT=IOS)
          IF (IOS .EQ. 0) THEN
            CONN = .TRUE.
            WRITE(*,'(1X,A,A)') 'Connected to ', PORT(1:LENTRM(PORT))
            WRITE(*,'(1X,A)') 'Reminder: set 115200-8N1 via MODE first.'
          ELSE
            WRITE(*,'(1X,A,I6)') 'Open failed, IOSTAT=', IOS
          END IF
        END IF
      ELSE IF (FIRST .EQ. 'close') THEN
        IF (CONN) CLOSE(LU)
        CONN = .FALSE.
        WRITE(*,'(1X,A)') 'Disconnected'
      ELSE IF (FIRST .EQ. 'list') THEN
        DO 200 I = 1, NT
          WRITE(*,'(1X,I3,2X,A)') I, TNAME(I)(1:LENTRM(TNAME(I)))
200     CONTINUE
      ELSE IF (FIRST .EQ. 'ending') THEN
        CALL TOLOW(REST)
        CALL SETEOL(REST)
      ELSE IF (FIRST .EQ. 'raw') THEN
        IF (LENTRM(REST) .GT. 0)
     &    CALL SNDCMD(LU, REST(1:LENTRM(REST)), CONN)
      ELSE IF (FIRST .EQ. 'ds1') THEN
        CALL SNDCMD(LU, '*DS1', CONN)
      ELSE IF (FIRST .EQ. 'ds0') THEN
        CALL SNDCMD(LU, '*DS0', CONN)
      ELSE IF (FIRST .EQ. 'dscheck') THEN
        CALL SNDCMD(LU, '?DS', CONN)
      ELSE IF (FIRST .EQ. 'midi') THEN
        CALL SPLTOK(REST, PORT, TAIL)
        IF (LENTRM(PORT) .EQ. 0) THEN
          WRITE(*,'(1X,A)') 'Usage:  midi <file.mid>'
        ELSE
          CALL MLOAD(PORT(1:LENTRM(PORT)), OKM)
          IF (.NOT. OKM) THEN
            WRITE(*,'(1X,A)') 'Could not open that file.'
          ELSE
            CALL MPARSE(OKM)
            IF (.NOT. OKM) THEN
              MLOADED = .FALSE.
              WRITE(*,'(1X,A)') 'Not a valid Standard MIDI File.'
            ELSE
              CALL MSORT
              MLOADED = .TRUE.
              WRITE(*,'(1X,A,I6,A,I5,A,I4)') 'Loaded ', NB,
     &          ' bytes,  events=', NE, ',  div=', DIVS
            END IF
          END IF
        END IF
      ELSE IF (FIRST .EQ. 'mraw') THEN
        IF (.NOT. MLOADED) THEN
          WRITE(*,'(1X,A)') 'Load a file first:  midi <file.mid>'
        ELSE IF (.NOT. CONN) THEN
          WRITE(*,'(1X,A)') 'Not connected.  open COMx  first.'
        ELSE
          DO 300 I = 1, NB
            WRITE(LU,'(A,$)') CHAR(MB(I))
300       CONTINUE
          WRITE(*,'(1X,A,I6,A)') 'Sent ', NB, ' raw bytes.'
        END IF
      ELSE IF (FIRST .EQ. 'mplay') THEN
        IF (.NOT. MLOADED) THEN
          WRITE(*,'(1X,A)') 'Load a file first:  midi <file.mid>'
        ELSE IF (.NOT. CONN) THEN
          WRITE(*,'(1X,A)') 'Not connected.  open COMx  first.'
        ELSE
          WRITE(*,'(1X,A)') 'Playing MIDI (timing approximate)...'
          CALL MWIRE(LU, .TRUE.)
          WRITE(*,'(1X,A)') 'Playback done.'
        END IF
      ELSE IF (FIRST .EQ. 'mdump') THEN
        CALL SPLTOK(REST, PORT, TAIL)
        IF (.NOT. MLOADED) THEN
          WRITE(*,'(1X,A)') 'Load a file first:  midi <file.mid>'
        ELSE IF (LENTRM(PORT) .EQ. 0) THEN
          WRITE(*,'(1X,A)') 'Usage:  mdump <outfile>'
        ELSE
          CALL MDUMPB(PORT(1:LENTRM(PORT)), 0, OKM)
          WRITE(*,'(1X,A)') 'Wrote play-mode wire stream.'
        END IF
      ELSE IF (FIRST .EQ. 'mdumpr') THEN
        CALL SPLTOK(REST, PORT, TAIL)
        IF (.NOT. MLOADED) THEN
          WRITE(*,'(1X,A)') 'Load a file first:  midi <file.mid>'
        ELSE IF (LENTRM(PORT) .EQ. 0) THEN
          WRITE(*,'(1X,A)') 'Usage:  mdumpr <outfile>'
        ELSE
          CALL MDUMPB(PORT(1:LENTRM(PORT)), 1, OKM)
          WRITE(*,'(1X,A)') 'Wrote raw file bytes.'
        END IF
      ELSE IF (FIRST .EQ. 'q') THEN
        CALL SPLTOK(REST, NUMT, TAIL)
        READ(NUMT,*,IOSTAT=IOS) IDX
        IF (IOS.NE.0 .OR. IDX.LT.1 .OR. IDX.GT.NT) THEN
          WRITE(*,'(1X,A)') 'Bad test number.  try: list'
        ELSE IF (LENTRM(TQRY(IDX)) .EQ. 0) THEN
          WRITE(*,'(1X,A)') 'No query command for that test.'
        ELSE
          CALL SNDCMD(LU, TQRY(IDX)(1:LENTRM(TQRY(IDX))), CONN)
        END IF
      ELSE IF (FIRST .EQ. 's') THEN
        CALL SPLTOK(REST, NUMT, PARM)
        READ(NUMT,*,IOSTAT=IOS) IDX
        IF (IOS.NE.0 .OR. IDX.LT.1 .OR. IDX.GT.NT) THEN
          WRITE(*,'(1X,A)') 'Usage:  s N [param]   (see: list)'
        ELSE IF (LENTRM(TWRT(IDX)) .EQ. 0) THEN
          WRITE(*,'(1X,A)') 'No write command for that test.'
        ELSE
          IF (LENTRM(PARM) .EQ. 0) PARM = TPDEF(IDX)
          CALL APPARM(TWRT(IDX), PARM, CMD)
          CALL SNDCMD(LU, CMD(1:LENTRM(CMD)), CONN)
        END IF
      ELSE IF (FIRST .EQ. 'x') THEN
        CALL SPLTOK(REST, NUMT, TAIL)
        READ(NUMT,*,IOSTAT=IOS) IDX
        IF (IOS.NE.0 .OR. IDX.LT.1 .OR. IDX.GT.NT) THEN
          WRITE(*,'(1X,A)') 'Bad test number.  try: list'
        ELSE IF (LENTRM(TSTP(IDX)) .EQ. 0) THEN
          WRITE(*,'(1X,A)') 'No stop command for that test.'
        ELSE
          CALL SNDCMD(LU, TSTP(IDX)(1:LENTRM(TSTP(IDX))), CONN)
        END IF
      ELSE
        WRITE(*,'(1X,A,A)') 'Unknown command: ',
     &    FIRST(1:LENTRM(FIRST))
      END IF
      GOTO 100

900   CONTINUE
      IF (CONN) CLOSE(LU)
      WRITE(*,'(1X,A)') 'Bye.'
      END

C ======================================================================
C  Catalog: initialise all-blank, then fill the real entries.  Codes are
C  preserved verbatim from the BDP V2 spec; some display names trimmed.
C ======================================================================
      SUBROUTINE FILLCT(TNAME, TQRY, TWRT, TSTP, TPDEF, MAXT)
      IMPLICIT NONE
      INTEGER MAXT, I
      CHARACTER*56 TNAME(MAXT)
      CHARACTER*10 TQRY(MAXT), TWRT(MAXT), TSTP(MAXT)
      CHARACTER*8  TPDEF(MAXT)

      DO 10 I = 1, MAXT
        TWRT(I)  = ' '
        TSTP(I)  = ' '
        TPDEF(I) = ' '
10    CONTINUE

      TNAME(1)='BDP Spec Version (00)'
      TNAME(2)='HW Build Info - Control Board (PH0)'
      TNAME(3)='HW Build Info - Power Board (PH1)'
      TNAME(4)='Solenoid Valve (DV)'
      TNAME(5)='Refill Pump (DD0)'
      TNAME(6)='Grey Water Pump (DD1)'
      TNAME(7)='Chemical Pump (DD2)'
      TNAME(8)='Recycle Pump (DD3)'
      TNAME(9)='E-Water Clean Water (EW0)'
      TNAME(10)='E-Water Grey Water (EW1)'
      TNAME(11)='Water Heater (WH)'
      TNAME(12)='Hot Air Heater (AH)'
      TNAME(13)='Clean Tank Status (DE0)'
      TNAME(14)='Grey Water Tank Status (DE2)'
      TNAME(15)='Chemical Tank Status (DE3)'
      TNAME(16)='Wash Tray Status (DE4)'
      TNAME(17)='Grey E-Water Module Status (DE5)'
      TNAME(18)='Dust Bin Full Switch (DE6)'
      TNAME(19)='Wash Tank Status (DF)'
      TNAME(20)='Water Heater NTC Temp (DT1)'
      TNAME(21)='Air Heater NTC Temp (DT2)'
      TNAME(22)='Recycle NTC Temp (DT3)'
      TNAME(23)='UI Button Key 1 (UI0)'
      TNAME(24)='UI Button Key 2 (UI1)'
      TNAME(25)='UI Button Key 3 (UI2)'
      TNAME(26)='Turbidity Sensor (RT)'
      TNAME(27)='Suction Motor (DC)'
      TNAME(28)='Power 12V Output (DA0)'
      TNAME(29)='Power 5V Output (DA1)'
      TNAME(30)='Power Charger Output (DA2)'
      TNAME(31)='Dry Fan Control (DL)'
      TNAME(32)='Shuttle Motor Go Home (DN)'
      TNAME(33)='IR UART Read from Robot (EC)'
      TNAME(34)='Enter/Exit Debug Mode (DS)'
      TNAME(35)='Z Signal LED (DZ)'
      TNAME(36)='UI LED (LG)'
      TNAME(37)='QR Code Information (WX)'
      TNAME(38)='Software Version (WZ)'

      TQRY(1)='?00'
      TQRY(2)='?PH0'
      TQRY(3)='?PH1'
      TQRY(4)='?DV'
      TQRY(5)='?DD0'
      TQRY(6)='?DD1'
      TQRY(7)='?DD2'
      TQRY(8)='?DD3'
      TQRY(9)='?EW0'
      TQRY(10)='?EW1'
      TQRY(11)='?WH'
      TQRY(12)='?AH'
      TQRY(13)='?DE0'
      TQRY(14)='?DE2'
      TQRY(15)='?DE3'
      TQRY(16)='?DE4'
      TQRY(17)='?DE5'
      TQRY(18)='?DE6'
      TQRY(19)='?DF'
      TQRY(20)='?DT1'
      TQRY(21)='?DT2'
      TQRY(22)='?DT3'
      TQRY(23)='?UI0'
      TQRY(24)='?UI1'
      TQRY(25)='?UI2'
      TQRY(26)='?RT'
      TQRY(27)='?DC'
      TQRY(28)='?DA0'
      TQRY(29)='?DA1'
      TQRY(30)='?DA2'
      TQRY(31)='?DL'
      TQRY(32)='?DN'
      TQRY(33)='?EC'
      TQRY(34)='?DS'
      TQRY(35)='?DZ'
      TQRY(36)='?LG'
      TQRY(37)='?WX'
      TQRY(38)='?WZ'

      TWRT(4)='*DV{p}'
      TWRT(5)='*DD0{p}'
      TWRT(6)='*DD1{p}'
      TWRT(7)='*DD2{p}'
      TWRT(8)='*DD3{p}'
      TWRT(9)='*EW0{p}'
      TWRT(10)='*EW1{p}'
      TWRT(11)='*WH{p}'
      TWRT(12)='*AH{p}'
      TWRT(27)='*DC{p}'
      TWRT(28)='*DA0{p}'
      TWRT(29)='*DA1{p}'
      TWRT(30)='*DA2{p}'
      TWRT(31)='*DL{p}'
      TWRT(32)='*DN{p}'
      TWRT(34)='*DS{p}'
      TWRT(35)='*DZ{p}'
      TWRT(36)='*LG{p}'

      TSTP(4)='*DV0'
      TSTP(5)='*DD000'
      TSTP(6)='*DD100'
      TSTP(7)='*DD200'
      TSTP(8)='*DD300'
      TSTP(9)='*EW00'
      TSTP(10)='*EW10'
      TSTP(11)='*WH00'
      TSTP(12)='*AH00'
      TSTP(27)='*DC0'
      TSTP(28)='*DA00'
      TSTP(29)='*DA10'
      TSTP(30)='*DA20'
      TSTP(31)='*DL0'
      TSTP(34)='*DS0'

      TPDEF(4)='1'
      TPDEF(5)='32'
      TPDEF(6)='32'
      TPDEF(7)='32'
      TPDEF(8)='32'
      TPDEF(9)='1'
      TPDEF(10)='1'
      TPDEF(11)='32'
      TPDEF(12)='64'
      TPDEF(27)='1'
      TPDEF(28)='1'
      TPDEF(29)='1'
      TPDEF(30)='1'
      TPDEF(31)='1'
      TPDEF(32)='32'
      TPDEF(34)='1'
      TPDEF(35)='9B'
      TPDEF(36)='1'
      END

C ======================================================================
C  Send CMD + line ending to the port, then read one reply record.
C ======================================================================
      SUBROUTINE SNDCMD(LU, CMD, CONN)
      IMPLICIT NONE
      INTEGER LU
      CHARACTER*(*) CMD
      LOGICAL CONN
      INTEGER NEOL
      CHARACTER*2 EOLCH
      COMMON /CFGI/ NEOL
      COMMON /CFGC/ EOLCH
      CHARACTER*256 REPLY
      CHARACTER*272 OUT
      INTEGER L, IOS, LENTRM
      EXTERNAL LENTRM

      IF (.NOT. CONN) THEN
        WRITE(*,'(1X,A)') 'Not connected.  open COMx  first.'
        RETURN
      END IF

      L = LENTRM(CMD)
      IF (NEOL .GT. 0) THEN
        OUT = CMD(1:L) // EOLCH(1:NEOL)
        WRITE(LU,'(A,$)',IOSTAT=IOS) OUT(1:L+NEOL)
      ELSE
        WRITE(LU,'(A,$)',IOSTAT=IOS) CMD(1:L)
      END IF
      WRITE(*,'(1X,A,A)') '-> ', CMD(1:L)

      READ(LU,'(A)',IOSTAT=IOS) REPLY
      IF (IOS .EQ. 0) THEN
        IF (LENTRM(REPLY) .GT. 0)
     &    WRITE(*,'(1X,A,A)') '<- ', REPLY(1:LENTRM(REPLY))
      END IF
      END

C ======================================================================
C  Substitute the {p} slot in a write template with parameter P.
C ======================================================================
      SUBROUTINE APPARM(TPL, P, OUT)
      IMPLICIT NONE
      CHARACTER*(*) TPL, P, OUT
      INTEGER IX, LT, LP, LENTRM
      EXTERNAL LENTRM
      IX = INDEX(TPL, '{p}')
      IF (IX .EQ. 0) THEN
        OUT = TPL
      ELSE
        LT = LENTRM(TPL)
        LP = LENTRM(P)
        IF (LP .LE. 0) LP = 1
        IF (IX+3 .LE. LT) THEN
          OUT = TPL(1:IX-1) // P(1:LP) // TPL(IX+3:LT)
        ELSE
          OUT = TPL(1:IX-1) // P(1:LP)
        END IF
      END IF
      END

C ======================================================================
C  Set the outgoing line ending from a keyword.
C ======================================================================
      SUBROUTINE SETEOL(NAME)
      IMPLICIT NONE
      CHARACTER*(*) NAME
      INTEGER NEOL, LENTRM
      CHARACTER*2 EOLCH
      COMMON /CFGI/ NEOL
      COMMON /CFGC/ EOLCH
      EXTERNAL LENTRM

      IF (NAME .EQ. 'cr' .OR. LENTRM(NAME) .EQ. 0) THEN
        EOLCH(1:1) = CHAR(13)
        NEOL = 1
      ELSE IF (NAME .EQ. 'lf') THEN
        EOLCH(1:1) = CHAR(10)
        NEOL = 1
      ELSE IF (NAME .EQ. 'crlf') THEN
        EOLCH(1:1) = CHAR(13)
        EOLCH(2:2) = CHAR(10)
        NEOL = 2
      ELSE IF (NAME .EQ. 'none') THEN
        NEOL = 0
      ELSE
        WRITE(*,'(1X,A)') 'Use:  ending cr | lf | crlf | none'
        RETURN
      END IF
      WRITE(*,'(1X,A)') 'Line ending updated.'
      END

C ======================================================================
C  Help text.
C ======================================================================
      SUBROUTINE HELP
      IMPLICIT NONE
      WRITE(*,'(1X,A)') 'Commands:'
      WRITE(*,'(1X,A)') '  ports          notes on finding COM ports'
      WRITE(*,'(1X,A)') '  open COMx      connect (set MODE first)'
      WRITE(*,'(1X,A)') '  close          disconnect'
      WRITE(*,'(1X,A)') '  list           show the test catalog'
      WRITE(*,'(1X,A)') '  q N            query test N      (?XX)'
      WRITE(*,'(1X,A)') '  s N [param]    write test N      (*XX<p>)'
      WRITE(*,'(1X,A)') '  x N            stop test N'
      WRITE(*,'(1X,A)') '  ds1 ds0 dscheck   enter/exit/confirm mode'
      WRITE(*,'(1X,A)') '  raw <text>     send a literal command'
      WRITE(*,'(1X,A)') '  ending cr|lf|crlf|none'
      WRITE(*,'(1X,A)') '  midi <file>    load a .mid file'
      WRITE(*,'(1X,A)') '  mraw           dump loaded .mid bytes raw'
      WRITE(*,'(1X,A)') '  mplay          play loaded .mid (timed)'
      WRITE(*,'(1X,A)') '  mdump <file>   write parsed wire stream'
      WRITE(*,'(1X,A)') '  mdumpr <file>  write raw .mid bytes'
      WRITE(*,'(1X,A)') '  help  quit'
      END

C ======================================================================
C  Length of S ignoring trailing blanks (no LEN_TRIM in F77).
C ======================================================================
      INTEGER FUNCTION LENTRM(S)
      IMPLICIT NONE
      CHARACTER*(*) S
      INTEGER I
      DO 10 I = LEN(S), 1, -1
        IF (S(I:I) .NE. ' ') THEN
          LENTRM = I
          RETURN
        END IF
10    CONTINUE
      LENTRM = 0
      END

C ======================================================================
C  Split S into its first blank-delimited token and the remainder.
C ======================================================================
      SUBROUTINE SPLTOK(S, FIRST, REST)
      IMPLICIT NONE
      CHARACTER*(*) S, FIRST, REST
      INTEGER I, J, N
      N = LEN(S)
      FIRST = ' '
      REST  = ' '
      I = 1
20    CONTINUE
      IF (I .GT. N) GOTO 80
      IF (S(I:I) .NE. ' ') GOTO 30
      I = I + 1
      GOTO 20
30    CONTINUE
      J = I
40    CONTINUE
      IF (J .GT. N) GOTO 50
      IF (S(J:J) .EQ. ' ') GOTO 50
      J = J + 1
      GOTO 40
50    CONTINUE
      FIRST = S(I:J-1)
C     skip the delimiter blanks so REST has no leading space
55    CONTINUE
      IF (J .GT. N) GOTO 80
      IF (S(J:J) .NE. ' ') GOTO 60
      J = J + 1
      GOTO 55
60    CONTINUE
      REST = S(J:N)
80    CONTINUE
      END

C ======================================================================
C  Lowercase S in place.
C ======================================================================
      SUBROUTINE TOLOW(S)
      IMPLICIT NONE
      CHARACTER*(*) S
      INTEGER I, C
      DO 10 I = 1, LEN(S)
        C = ICHAR(S(I:I))
        IF (C .GE. 65 .AND. C .LE. 90) S(I:I) = CHAR(C + 32)
10    CONTINUE
      END

C ======================================================================
C  ===================  MIDI feature (added)  =========================
C  A .mid file is parsed into a tick-ordered event list held in COMMON,
C  then either dumped raw or 'played' as timed channel-voice messages.
C ======================================================================

C  Read a MIDI variable-length quantity at MB(P); advance P past it.
      SUBROUTINE GVLQ(MB, P, VAL)
      IMPLICIT NONE
      INTEGER MB(*), P, VAL, B
      VAL = 0
10    CONTINUE
      B = MB(P)
      P = P + 1
      VAL = VAL * 128 + MOD(B, 128)
      IF (B .GE. 128) GOTO 10
      END

C  Load a file into the MB byte buffer (1 byte per direct-access record).
      SUBROUTINE MLOAD(FNAME, OK)
      IMPLICIT NONE
      CHARACTER*(*) FNAME
      LOGICAL OK
      INTEGER MAXB
      PARAMETER (MAXB=20000)
      INTEGER NB, MB(MAXB)
      COMMON /MBUF/ NB, MB
      INTEGER IOS, V
      CHARACTER*1 CH
      OPEN(UNIT=21, FILE=FNAME, FORM='UNFORMATTED', ACCESS='DIRECT',
     &     RECL=1, STATUS='OLD', IOSTAT=IOS)
      IF (IOS .NE. 0) THEN
        OK = .FALSE.
        RETURN
      END IF
      NB = 0
10    CONTINUE
      READ(21, REC=NB+1, IOSTAT=IOS) CH
      IF (IOS .NE. 0) GOTO 20
      NB = NB + 1
      V = ICHAR(CH)
      IF (V .LT. 0) V = V + 256
      MB(NB) = V
      IF (NB .LT. MAXB) GOTO 10
20    CONTINUE
      CLOSE(21)
      OK = .TRUE.
      END

C  Parse MB(1:NB) into the event arrays.  Mirrors parse_midi() in the
C  Python version: meta tempo kept, channel-voice messages kept, sysex
C  and other meta skipped.  Running status supported.
      SUBROUTINE MPARSE(OK)
      IMPLICIT NONE
      LOGICAL OK
      INTEGER MAXB, MAXE
      PARAMETER (MAXB=20000, MAXE=8000)
      INTEGER NB, MB(MAXB)
      COMMON /MBUF/ NB, MB
      INTEGER DIVS, NE, ET(MAXE), EK(MAXE), E0(MAXE), E1(MAXE),
     &        E2(MAXE), ENB(MAXE), ETP(MAXE)
      COMMON /MEVT/ DIVS, NE, ET, EK, E0, E1, E2, ENB, ETP
      INTEGER P, NTRK, T, ENDP, ATICK, STAT, B, DT, MT, LN, HI, ND, TMP

      OK = .FALSE.
      IF (NB .LT. 14) RETURN
      IF (MB(1).NE.77 .OR. MB(2).NE.84 .OR. MB(3).NE.104
     &    .OR. MB(4).NE.100) RETURN
      NTRK = MB(11)*256 + MB(12)
      DIVS = MB(13)*256 + MB(14)
      IF (DIVS .LE. 0) RETURN
      P = 15
      NE = 0

      DO 100 T = 1, NTRK
        IF (P+7 .GT. NB) GOTO 110
        IF (MB(P).NE.77 .OR. MB(P+1).NE.84 .OR. MB(P+2).NE.114
     &      .OR. MB(P+3).NE.107) GOTO 110
        LN = MB(P+4)*16777216 + MB(P+5)*65536 + MB(P+6)*256 + MB(P+7)
        P = P + 8
        ENDP = P + LN
        ATICK = 0
        STAT = 0
50      CONTINUE
        IF (P .GE. ENDP) GOTO 100
        CALL GVLQ(MB, P, DT)
        ATICK = ATICK + DT
        B = MB(P)
        IF (B .GE. 128) THEN
          STAT = B
          P = P + 1
        END IF
        IF (STAT .EQ. 255) THEN
          MT = MB(P)
          P = P + 1
          CALL GVLQ(MB, P, LN)
          IF (MT.EQ.81 .AND. LN.EQ.3) THEN
            TMP = MB(P)*65536 + MB(P+1)*256 + MB(P+2)
            IF (NE .LT. MAXE) THEN
              NE = NE + 1
              ET(NE)=ATICK
              EK(NE)=1
              E0(NE)=0
              E1(NE)=0
              E2(NE)=0
              ENB(NE)=0
              ETP(NE)=TMP
            END IF
          END IF
          IF (MT .EQ. 47) THEN
            P = ENDP
            GOTO 100
          END IF
          P = P + LN
        ELSE IF (STAT.EQ.240 .OR. STAT.EQ.247) THEN
          CALL GVLQ(MB, P, LN)
          P = P + LN
        ELSE
          HI = (STAT/16)*16
          IF (HI.EQ.192 .OR. HI.EQ.208) THEN
            ND = 1
          ELSE
            ND = 2
          END IF
          IF (NE .LT. MAXE) THEN
            NE = NE + 1
            ET(NE)=ATICK
            EK(NE)=0
            E0(NE)=STAT
            E1(NE)=MB(P)
            IF (ND .EQ. 2) THEN
              E2(NE)=MB(P+1)
            ELSE
              E2(NE)=-1
            END IF
            ENB(NE)=ND
            ETP(NE)=0
          END IF
          P = P + ND
        END IF
        GOTO 50
100   CONTINUE
110   CONTINUE
      OK = .TRUE.
      END

C  Stable insertion sort of the event arrays by absolute tick, so the
C  merged multi-track stream is time-ordered (ties keep track order).
      SUBROUTINE MSORT
      IMPLICIT NONE
      INTEGER MAXE
      PARAMETER (MAXE=8000)
      INTEGER DIVS, NE, ET(MAXE), EK(MAXE), E0(MAXE), E1(MAXE),
     &        E2(MAXE), ENB(MAXE), ETP(MAXE)
      COMMON /MEVT/ DIVS, NE, ET, EK, E0, E1, E2, ENB, ETP
      INTEGER I, J, KT, KK, K0, K1, K2, KN, KP
      DO 20 I = 2, NE
        KT=ET(I)
        KK=EK(I)
        K0=E0(I)
        K1=E1(I)
        K2=E2(I)
        KN=ENB(I)
        KP=ETP(I)
        J = I - 1
10      CONTINUE
        IF (J .LT. 1) GOTO 15
        IF (ET(J) .LE. KT) GOTO 15
        ET(J+1)=ET(J)
        EK(J+1)=EK(J)
        E0(J+1)=E0(J)
        E1(J+1)=E1(J)
        E2(J+1)=E2(J)
        ENB(J+1)=ENB(J)
        ETP(J+1)=ETP(J)
        J = J - 1
        GOTO 10
15      CONTINUE
        ET(J+1)=KT
        EK(J+1)=KK
        E0(J+1)=K0
        E1(J+1)=K1
        E2(J+1)=K2
        ENB(J+1)=KN
        ETP(J+1)=KP
20    CONTINUE
      END

C  Emit the play-mode wire stream to formatted unit LU, with optional
C  (approximate) inter-event timing.  Ends with all-notes-off.
      SUBROUTINE MWIRE(LU, DOWAIT)
      IMPLICIT NONE
      INTEGER LU
      LOGICAL DOWAIT
      INTEGER MAXE
      PARAMETER (MAXE=8000)
      INTEGER DIVS, NE, ET(MAXE), EK(MAXE), E0(MAXE), E1(MAXE),
     &        E2(MAXE), ENB(MAXE), ETP(MAXE)
      COMMON /MEVT/ DIVS, NE, ET, EK, E0, E1, E2, ENB, ETP
      INTEGER I, LAST, DELTA, MS
      DOUBLE PRECISION TEMPO
      TEMPO = 500000.0D0
      LAST = 0
      DO 20 I = 1, NE
        DELTA = ET(I) - LAST
        LAST = ET(I)
        IF (DOWAIT .AND. DELTA .GT. 0) THEN
          MS = NINT(DBLE(DELTA) * TEMPO / DBLE(DIVS) / 1000.0D0)
          CALL MDELAY(MS)
        END IF
        IF (EK(I) .EQ. 1) THEN
          TEMPO = DBLE(ETP(I))
        ELSE
          WRITE(LU,'(A,$)') CHAR(E0(I))
          WRITE(LU,'(A,$)') CHAR(E1(I))
          IF (ENB(I) .EQ. 2) WRITE(LU,'(A,$)') CHAR(E2(I))
        END IF
20    CONTINUE
      DO 30 I = 0, 15
        WRITE(LU,'(A,$)') CHAR(176 + I)
        WRITE(LU,'(A,$)') CHAR(123)
        WRITE(LU,'(A,$)') CHAR(0)
30    CONTINUE
      END

C  Write exact bytes to a file via direct access (for inspection/tests):
C  WHICH=0 -> play-mode wire stream;  WHICH=1 -> raw .mid bytes.
      SUBROUTINE MDUMPB(FNAME, WHICH, OK)
      IMPLICIT NONE
      CHARACTER*(*) FNAME
      INTEGER WHICH
      LOGICAL OK
      INTEGER MAXB, MAXE
      PARAMETER (MAXB=20000, MAXE=8000)
      INTEGER NB, MB(MAXB)
      COMMON /MBUF/ NB, MB
      INTEGER DIVS, NE, ET(MAXE), EK(MAXE), E0(MAXE), E1(MAXE),
     &        E2(MAXE), ENB(MAXE), ETP(MAXE)
      COMMON /MEVT/ DIVS, NE, ET, EK, E0, E1, E2, ENB, ETP
      INTEGER IOS, I, K, C
      OPEN(UNIT=22, FILE=FNAME, FORM='UNFORMATTED', ACCESS='DIRECT',
     &     RECL=1, STATUS='UNKNOWN', IOSTAT=IOS)
      IF (IOS .NE. 0) THEN
        OK = .FALSE.
        RETURN
      END IF
      K = 0
      IF (WHICH .EQ. 1) THEN
        DO 10 I = 1, NB
          K = K + 1
          WRITE(22, REC=K) CHAR(MB(I))
10      CONTINUE
      ELSE
        DO 20 I = 1, NE
          IF (EK(I) .EQ. 0) THEN
            K = K + 1
            WRITE(22, REC=K) CHAR(E0(I))
            K = K + 1
            WRITE(22, REC=K) CHAR(E1(I))
            IF (ENB(I) .EQ. 2) THEN
              K = K + 1
              WRITE(22, REC=K) CHAR(E2(I))
            END IF
          END IF
20      CONTINUE
        DO 30 C = 0, 15
          K = K + 1
          WRITE(22, REC=K) CHAR(176 + C)
          K = K + 1
          WRITE(22, REC=K) CHAR(123)
          K = K + 1
          WRITE(22, REC=K) CHAR(0)
30      CONTINUE
      END IF
      CLOSE(22)
      OK = .TRUE.
      END

C  Crude busy-wait delay of about MS milliseconds (no portable sleep in
C  F77).  Calibration is approximate; tune the multiplier for your CPU.
      SUBROUTINE MDELAY(MS)
      IMPLICIT NONE
      INTEGER MS, I, NITER
      DOUBLE PRECISION X
      IF (MS .LE. 0) RETURN
      NITER = MS * 200000
      X = 1.0D0
      DO 10 I = 1, NITER
        X = X + DSQRT(DBLE(I) + 1.0D0)
10    CONTINUE
      IF (X .LT. 0.0D0) WRITE(*,*) X
      END
