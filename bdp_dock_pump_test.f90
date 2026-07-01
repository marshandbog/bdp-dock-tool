! ============================================================================
!  RV2900WD Dock BDP Pump Test Tool  --  Fortran / Win32 edition
!  ---------------------------------------------------------------------------
!  Port of bdp_dock_pump_test.py.  The Python original was a Tkinter GUI with a
!  background serial-reader thread; Fortran has neither a standard GUI nor a
!  serial library nor portable threads, so this is a console REPL that talks to
!  a Windows COM port directly through the Win32 API (kernel32) via
!  ISO_C_BINDING.  The BDP command catalog and the ?/ * / $ protocol are
!  preserved verbatim.
!
!  Build (MinGW-w64 gfortran on Windows):
!      gfortran -O2 -o bdp_dock_pump_test.exe bdp_dock_pump_test.f90
!  kernel32 is linked by default, so no extra -l flags are needed.
!
!  Run:
!      bdp_dock_pump_test.exe
!  then type `help`.
! ============================================================================

module bdp_win32
   use iso_c_binding
   implicit none

   ! ----- Win32 constants (bit patterns built with ISHFT to stay in int32) ---
   integer(c_int32_t), parameter :: GENERIC_READ  = ishft(int(z'80', c_int32_t), 24) ! 0x80000000
   integer(c_int32_t), parameter :: GENERIC_WRITE = ishft(int(z'40', c_int32_t), 24) ! 0x40000000
   integer(c_int32_t), parameter :: OPEN_EXISTING = 3
   integer(c_int32_t), parameter :: FILE_ATTR_NORMAL = 128
   integer(c_intptr_t), parameter :: INVALID_HANDLE = -1_c_intptr_t

   ! ----- DCB serial-control block (28 bytes, matches Win32 layout) ----------
   type, bind(C) :: DCB
      integer(c_int32_t) :: DCBlength
      integer(c_int32_t) :: BaudRate
      integer(c_int32_t) :: flags          ! packed bitfields (fBinary = bit 0)
      integer(c_int16_t) :: wReserved
      integer(c_int16_t) :: XonLim
      integer(c_int16_t) :: XoffLim
      integer(c_int8_t)  :: ByteSize
      integer(c_int8_t)  :: Parity
      integer(c_int8_t)  :: StopBits
      integer(c_int8_t)  :: XonChar
      integer(c_int8_t)  :: XoffChar
      integer(c_int8_t)  :: ErrorChar
      integer(c_int8_t)  :: EofChar
      integer(c_int8_t)  :: EvtChar
      integer(c_int16_t) :: wReserved1
   end type DCB

   type, bind(C) :: COMMTIMEOUTS
      integer(c_int32_t) :: ReadIntervalTimeout
      integer(c_int32_t) :: ReadTotalTimeoutMultiplier
      integer(c_int32_t) :: ReadTotalTimeoutConstant
      integer(c_int32_t) :: WriteTotalTimeoutMultiplier
      integer(c_int32_t) :: WriteTotalTimeoutConstant
   end type COMMTIMEOUTS

   interface
      function CreateFileA(name, access, share, sec, disp, attrs, templ) &
                           bind(C, name="CreateFileA")
         import :: c_char, c_int32_t, c_ptr, c_intptr_t
         character(kind=c_char), intent(in) :: name(*)
         integer(c_int32_t), value :: access, share, disp, attrs
         type(c_ptr), value :: sec, templ
         integer(c_intptr_t) :: CreateFileA
      end function CreateFileA

      function CloseHandle(h) bind(C, name="CloseHandle")
         import :: c_intptr_t, c_int
         integer(c_intptr_t), value :: h
         integer(c_int) :: CloseHandle
      end function CloseHandle

      function ReadFile(h, buf, n, nread, ov) bind(C, name="ReadFile")
         import :: c_intptr_t, c_char, c_int32_t, c_ptr, c_int
         integer(c_intptr_t), value :: h
         character(kind=c_char) :: buf(*)
         integer(c_int32_t), value :: n
         integer(c_int32_t) :: nread
         type(c_ptr), value :: ov
         integer(c_int) :: ReadFile
      end function ReadFile

      function WriteFile(h, buf, n, nwritten, ov) bind(C, name="WriteFile")
         import :: c_intptr_t, c_char, c_int32_t, c_ptr, c_int
         integer(c_intptr_t), value :: h
         character(kind=c_char), intent(in) :: buf(*)
         integer(c_int32_t), value :: n
         integer(c_int32_t) :: nwritten
         type(c_ptr), value :: ov
         integer(c_int) :: WriteFile
      end function WriteFile

      function GetCommState(h, dcbptr) bind(C, name="GetCommState")
         import :: c_intptr_t, DCB, c_int
         integer(c_intptr_t), value :: h
         type(DCB) :: dcbptr
         integer(c_int) :: GetCommState
      end function GetCommState

      function SetCommState(h, dcbptr) bind(C, name="SetCommState")
         import :: c_intptr_t, DCB, c_int
         integer(c_intptr_t), value :: h
         type(DCB) :: dcbptr
         integer(c_int) :: SetCommState
      end function SetCommState

      function SetCommTimeouts(h, ct) bind(C, name="SetCommTimeouts")
         import :: c_intptr_t, COMMTIMEOUTS, c_int
         integer(c_intptr_t), value :: h
         type(COMMTIMEOUTS) :: ct
         integer(c_int) :: SetCommTimeouts
      end function SetCommTimeouts

      function QueryDosDeviceA(name, buffer, maxlen) bind(C, name="QueryDosDeviceA")
         import :: c_ptr, c_char, c_int32_t
         type(c_ptr), value :: name
         character(kind=c_char) :: buffer(*)
         integer(c_int32_t), value :: maxlen
         integer(c_int32_t) :: QueryDosDeviceA
      end function QueryDosDeviceA

      function GetLastError() bind(C, name="GetLastError")
         import :: c_int32_t
         integer(c_int32_t) :: GetLastError
      end function GetLastError

      subroutine Sleep(ms) bind(C, name="Sleep")
         import :: c_int32_t
         integer(c_int32_t), value :: ms
      end subroutine Sleep
   end interface

   ! ----- module-level serial state -----------------------------------------
   integer(c_intptr_t) :: ser_handle = INVALID_HANDLE
   logical             :: ser_open   = .false.
   character(len=2)    :: eol_chars  = '  '
   integer             :: eol_len    = 1

contains

   subroutine init_eol()
      eol_chars(1:1) = char(13)   ! default CR, per BDP Command Spec V2
      eol_len = 1
   end subroutine init_eol

   ! Configure 115200-8N1 by reading the current DCB, tweaking it, writing back.
   logical function connect(port)
      character(len=*), intent(in) :: port
      character(len=:), allocatable :: devname
      character(kind=c_char), allocatable :: cname(:)
      type(DCB) :: d
      type(COMMTIMEOUTS) :: ct
      integer :: i, n
      connect = .false.
      if (ser_open) call disconnect()

      ! "\\.\COMx" works for COM1..COM256; Fortran does not escape backslashes.
      devname = '\\.\' // trim(port)
      n = len_trim(devname)
      allocate(cname(n+1))
      do i = 1, n
         cname(i) = devname(i:i)
      end do
      cname(n+1) = c_null_char

      ser_handle = CreateFileA(cname, ior(GENERIC_READ, GENERIC_WRITE), 0_c_int32_t, &
                               c_null_ptr, OPEN_EXISTING, FILE_ATTR_NORMAL, c_null_ptr)
      if (ser_handle == INVALID_HANDLE) then
         print '(A,I0,A)', 'Connection failed (Win32 error ', GetLastError(), &
                           '). Check the port name and that nothing else owns it.'
         return
      end if

      if (GetCommState(ser_handle, d) == 0) then
         print '(A,I0,A)', 'GetCommState failed (error ', GetLastError(), ').'
         call disconnect(); return
      end if
      d%DCBlength = 28
      d%BaudRate  = 115200
      d%ByteSize  = 8_c_int8_t
      d%Parity    = 0_c_int8_t                 ! NOPARITY
      d%StopBits  = 0_c_int8_t                 ! ONESTOPBIT
      d%flags     = ior(d%flags, 1_c_int32_t)  ! fBinary = 1
      d%flags     = iand(d%flags, not(2_c_int32_t)) ! fParity = 0
      if (SetCommState(ser_handle, d) == 0) then
         print '(A,I0,A)', 'SetCommState failed (error ', GetLastError(), ').'
         call disconnect(); return
      end if

      ! Mirror pyserial timeout=0.2s so reads return promptly.
      ct%ReadIntervalTimeout = 50
      ct%ReadTotalTimeoutMultiplier = 0
      ct%ReadTotalTimeoutConstant = 200
      ct%WriteTotalTimeoutMultiplier = 0
      ct%WriteTotalTimeoutConstant = 0
      if (SetCommTimeouts(ser_handle, ct) == 0) then
         print '(A,I0,A)', 'SetCommTimeouts failed (error ', GetLastError(), ').'
         call disconnect(); return
      end if

      ser_open = .true.
      connect  = .true.
      print '(A)', 'Connected to ' // trim(port) // ' at 115200-8N1'
   end function connect

   subroutine disconnect()
      integer(c_int) :: rc
      if (ser_handle /= INVALID_HANDLE) rc = CloseHandle(ser_handle)
      ser_handle = INVALID_HANDLE
      ser_open = .false.
   end subroutine disconnect

   ! Write a command plus the selected line ending, then drain the reply.
   subroutine send_cmd(cmd)
      character(len=*), intent(in) :: cmd
      character(kind=c_char), allocatable :: buf(:)
      integer(c_int32_t) :: nwritten, total
      integer(c_int) :: rc
      integer :: i, L
      if (.not. ser_open) then
         print '(A)', 'Not connected. Use:  open COMx'
         return
      end if
      L = len_trim(cmd)
      total = int(L + eol_len, c_int32_t)
      allocate(buf(L + eol_len))
      do i = 1, L
         buf(i) = cmd(i:i)
      end do
      do i = 1, eol_len
         buf(L + i) = eol_chars(i:i)
      end do
      rc = WriteFile(ser_handle, buf, total, nwritten, c_null_ptr)
      if (rc == 0) then
         print '(A,I0,A)', 'Send error (Win32 error ', GetLastError(), ').'
         return
      end if
      print '(A)', '-> ' // trim(cmd)
      call drain_reply()
   end subroutine send_cmd

   ! Poll for a response for ~0.6s and print whatever arrives.
   subroutine drain_reply()
      character(kind=c_char) :: cbuf(512)
      character(len=512) :: line
      integer(c_int32_t) :: nread
      integer(c_int) :: rc
      integer :: attempt, i, c
      do attempt = 1, 3
         nread = 0
         rc = ReadFile(ser_handle, cbuf, 512_c_int32_t, nread, c_null_ptr)
         if (rc /= 0 .and. nread > 0) then
            line = ''
            do i = 1, int(nread)
               c = ichar(cbuf(i))
               if (c < 32) then
                  line(i:i) = ' '        ! flatten CR/LF/control chars
               else
                  line(i:i) = char(c)
               end if
            end do
            print '(A)', '<- ' // trim(line(1:int(nread)))
            return
         end if
         call Sleep(60_c_int32_t)
      end do
   end subroutine drain_reply

   ! Enumerate COM ports via QueryDosDevice (NULL -> all DOS device names).
   subroutine list_ports()
      character(kind=c_char), allocatable :: buf(:)
      character(len=64) :: tok
      integer(c_int32_t) :: nret
      integer :: i, tlen, found
      allocate(buf(65536))
      nret = QueryDosDeviceA(c_null_ptr, buf, 65536_c_int32_t)
      if (nret == 0) then
         print '(A,I0,A)', 'QueryDosDevice failed (error ', GetLastError(), ').'
         return
      end if
      print '(A)', 'Available COM ports:'
      found = 0
      tok = ''
      tlen = 0
      do i = 1, int(nret)
         if (ichar(buf(i)) == 0) then
            if (tlen >= 3) then
               if (tok(1:3) == 'COM') then
                  print '(A)', '   ' // trim(tok(1:tlen))
                  found = found + 1
               end if
            end if
            tok = ''
            tlen = 0
         else if (tlen < len(tok)) then
            tlen = tlen + 1
            tok(tlen:tlen) = char(ichar(buf(i)))
         end if
      end do
      if (found == 0) print '(A)', '   (none found)'
   end subroutine list_ports

   ! Write n raw byte values (0..255) to the open port. Used by MIDI send.
   subroutine ser_write_raw(bytes, n)
      integer, intent(in) :: bytes(*)
      integer, intent(in) :: n
      character(kind=c_char) :: cb(n)
      integer(c_int32_t) :: nw
      integer(c_int) :: rc
      integer :: i
      if (.not. ser_open) return
      do i = 1, n
         cb(i) = char(bytes(i))
      end do
      rc = WriteFile(ser_handle, cb, int(n, c_int32_t), nw, c_null_ptr)
   end subroutine ser_write_raw

end module bdp_win32

! ============================================================================
module bdp_catalog
   implicit none

   type :: bdp_test
      character(len=56)  :: name
      character(len=10)  :: query
      character(len=10)  :: wtpl     ! '' = no write command; '{p}' = param slot
      character(len=10)  :: stopc    ! '' = no stop command
      logical            :: needs_param
      character(len=44)  :: plabel
      character(len=8)   :: pdef
      character(len=140) :: hint
   end type bdp_test

   integer, parameter :: NTESTS = 38
   type(bdp_test) :: tests(NTESTS)

contains

   function mk(name, query, wtpl, stopc, needs_param, plabel, pdef, hint) result(t)
      character(len=*), intent(in) :: name, query, wtpl, stopc, plabel, pdef, hint
      logical, intent(in) :: needs_param
      type(bdp_test) :: t
      t%name = name; t%query = query; t%wtpl = wtpl; t%stopc = stopc
      t%needs_param = needs_param; t%plabel = plabel; t%pdef = pdef; t%hint = hint
   end function mk

   subroutine build_catalog()
      integer :: n
      n = 0
      n=n+1; tests(n)=mk('BDP Spec Version (00)','?00','','',.false.,'','', &
         'Returns $00<version>, e.g. $00RevB. BDP tool only.')
      n=n+1; tests(n)=mk('HW Build Info - Control Board (PH0)','?PH0','','',.false.,'','', &
         'Returns $PH0<string>, e.g. $PH0EB01.')
      n=n+1; tests(n)=mk('HW Build Info - Power Board (PH1)','?PH1','','',.false.,'','', &
         'Returns $PH1<string>, e.g. $PH1PWUS (US power board).')
      n=n+1; tests(n)=mk('Solenoid Valve (DV)','?DV','*DV{p}','*DV0',.true., &
         'State (0=Off, 1=On)','1','Query returns $DV<state><current>. e.g. $DV1093 = on, 147mA.')
      n=n+1; tests(n)=mk('Refill Pump - ON/OFF (DD0)','?DD0','*DD0{p}','*DD000',.true., &
         'Duty cycle (hex, 00=off)','32','Returns $DD0<dutycycle><current>.')
      n=n+1; tests(n)=mk('Grey Water Pump - ON/OFF (DD1)','?DD1','*DD1{p}','*DD100',.true., &
         'Duty cycle (hex, 00=off)','32','Returns $DD1<dutycycle><current>.')
      n=n+1; tests(n)=mk('Chemical Pump - ON/OFF (DD2)','?DD2','*DD2{p}','*DD200',.true., &
         'Duty cycle (hex, 00=off)','32','Returns $DD2<dutycycle><current>.')
      n=n+1; tests(n)=mk('Recycle Pump - ON/OFF (DD3)','?DD3','*DD3{p}','*DD300',.true., &
         'Duty cycle (hex, 00=off)','32','Returns $DD3<dutycycle><current>.')
      n=n+1; tests(n)=mk('E-Water Control - Clean Water (EW0)','?EW0','*EW0{p}','*EW00',.true., &
         'State (0=close,1=+,2=-)','1','Query returns $EW0<voltage><current limit>.')
      n=n+1; tests(n)=mk('E-Water Control - Grey Water (EW1)','?EW1','*EW1{p}','*EW10',.true., &
         'State (0=close,1=+,2=-)','1','Query returns $EW1<voltage><current limit>.')
      n=n+1; tests(n)=mk('Water Heater (WH)','?WH','*WH{p}','*WH00',.true., &
         'Duty cycle (hex, 00=off)','32','Returns $WH<dutycycle><actual temperature>.')
      n=n+1; tests(n)=mk('Hot Air Heater (AH)','?AH','*AH{p}','*AH00',.true., &
         'Duty cycle (hex, 00=off)','64','Returns $AH<dutycycle><actual temperature>.')
      n=n+1; tests(n)=mk('Water Tank Status - Clean Tank (DE0)','?DE0','','',.false.,'','', &
         'Returns $DE0<status>. 0=empty,1=full,2=present,3=not present.')
      n=n+1; tests(n)=mk('Water Tank Status - Grey Water Tank (DE2)','?DE2','','',.false.,'','', &
         'Returns $DE2<status>.')
      n=n+1; tests(n)=mk('Water Tank Status - Chemical Tank (DE3)','?DE3','','',.false.,'','', &
         'Returns $DE3<status>.')
      n=n+1; tests(n)=mk('Water Tank Status - Wash Tray (DE4)','?DE4','','',.false.,'','', &
         'Returns $DE4<status>. e.g. $DE40 = wash tray empty.')
      n=n+1; tests(n)=mk('Water Tank Status - Grey E-Water Module (DE5)','?DE5','','',.false.,'','', &
         'Returns $DE5<status>. e.g. $DE52 = module installed.')
      n=n+1; tests(n)=mk('Water Tank Status - Dust Bin Full Switch (DE6)','?DE6','','',.false.,'','', &
         'Returns $DE6<status>.')
      n=n+1; tests(n)=mk('Wash Tank Status (DF)','?DF','','',.false.,'','', &
         'Returns $DF<control_1><control_2><value> (resistance in kOhm).')
      n=n+1; tests(n)=mk('Temperature - Water Heater NTC (DT1)','?DT1','','',.false.,'','', &
         'Returns $DT1<value>, e.g. $DT120 = 32C.')
      n=n+1; tests(n)=mk('Temperature - Air Heater NTC (DT2)','?DT2','','',.false.,'','', &
         'Returns $DT2<value>.')
      n=n+1; tests(n)=mk('Temperature - Recycle NTC (DT3)','?DT3','','',.false.,'','', &
         'Returns $DT3<value>.')
      n=n+1; tests(n)=mk('UI Button - Key 1 (UI0)','?UI0','','',.false.,'','', &
         'Returns $UI0<value>. 0=released, 1=pressed.')
      n=n+1; tests(n)=mk('UI Button - Key 2 (UI1)','?UI1','','',.false.,'','', &
         'Returns $UI1<value>.')
      n=n+1; tests(n)=mk('UI Button - Key 3 (UI2)','?UI2','','',.false.,'','', &
         'Returns $UI2<value>.')
      n=n+1; tests(n)=mk('Turbidity Sensor (RT)','?RT','','',.false.,'','', &
         'Returns $RT<value>. Experimental - still in research per spec.')
      n=n+1; tests(n)=mk('Suction Motor (DC)','?DC','*DC{p}','*DC0',.true., &
         'State (0=Off, 1=On)','1','Query returns $DC<state>, e.g. $DC1 = motor on.')
      n=n+1; tests(n)=mk('Power Mode - 12V Output (DA0)','?DA0','*DA0{p}','*DA00',.true., &
         'State (0=Off, 1=On)','1','Returns $DA0<state><current>.')
      n=n+1; tests(n)=mk('Power Mode - 5V Output (DA1)','?DA1','*DA1{p}','*DA10',.true., &
         'State (0=Off, 1=On)','1','Returns $DA1<state><current>.')
      n=n+1; tests(n)=mk('Power Mode - Charger Output (DA2)','?DA2','*DA2{p}','*DA20',.true., &
         'State (0=Off, 1=On)','1','Returns $DA2<state><current>. e.g. $DA21021 = charger on, 33mA.')
      n=n+1; tests(n)=mk('Dry Fan Control (DL)','?DL','*DL{p}','*DL0',.true., &
         'State (0=Off, 1=On)','1','Returns $DL<state><current>. Requires charging limit switch triggered.')
      n=n+1; tests(n)=mk('Shuttle Motor Go Home (DN)','?DN','*DN{p}','',.true., &
         'Duty cycle (hex)','32','Query returns $DN<home_switch>. 1=closed (shuttle home).')
      n=n+1; tests(n)=mk('IR UART - Read from Robot (EC)','?EC','','',.false.,'','', &
         'Returns $EC<string> (16 chars) received from robot.')
      n=n+1; tests(n)=mk('Enter/Exit Debug Mode (DS)','?DS','*DS{p}','*DS0',.true., &
         'State (0=off, 1=on)','1','Returns $DS<state>. Main Enter/Exit Test Mode command.')
      n=n+1; tests(n)=mk('Z Signal LED (DZ)','?DZ','*DZ{p}','',.true., &
         'Z signal (hex 00-FF)','9B','Returns $DZ<value>. Spec is inverted (00=on, >0=off).')
      n=n+1; tests(n)=mk('UI LED (LG)','?LG','*LG{p}','',.true., &
         'Mode (0=off,1=on,2=flash,B/G/R)','1','Returns $LG<mode>.')
      n=n+1; tests(n)=mk('QR Code Information (WX)','?WX','','',.false.,'','', &
         'Returns $WX<model><serial_number>.')
      n=n+1; tests(n)=mk('Software Version (WZ)','?WZ','','',.false.,'','', &
         'Returns $WZ<AA><BB><CC><build date>.')
   end subroutine build_catalog

   ! Substitute the {p} slot in a write template with the supplied parameter.
   function apply_param(tpl, p) result(r)
      character(len=*), intent(in) :: tpl, p
      character(len=64) :: r
      integer :: ix
      ix = index(tpl, '{p}')
      if (ix > 0) then
         r = tpl(1:ix-1) // trim(p) // tpl(ix+3:len_trim(tpl))
      else
         r = trim(tpl)
      end if
   end function apply_param

end module bdp_catalog

! ============================================================================
!  MIDI: parse a Standard MIDI File into a tick-ordered event list, then dump
!  it raw or 'play' it as timed channel-voice messages.  Load/parse/dump use
!  portable stream I/O; only the live send touches the Win32 serial port.
!  Logic mirrors parse_midi()/midi_to_wire_schedule() in the Python version.
! ============================================================================
module bdp_midi
   use iso_fortran_env, only: int8
   use bdp_win32, only: ser_open, ser_write_raw, Sleep
   use iso_c_binding, only: c_int32_t
   implicit none

   integer :: nb = 0                       ! number of file bytes
   integer, allocatable :: mbuf(:)         ! file bytes, values 0..255
   integer :: division = 0, ne = 0         ! ticks/quarter, number of events
   integer, allocatable :: et(:), ek(:), e0(:), e1(:), e2(:), enb(:), etp(:)
   logical :: loaded = .false.

contains

   subroutine mload(fname, ok)
      character(len=*), intent(in) :: fname
      logical, intent(out) :: ok
      integer :: u, ios, i, v
      integer(int8), allocatable :: raw(:)
      ok = .false.
      open(newunit=u, file=fname, access='stream', form='unformatted', &
           status='old', iostat=ios)
      if (ios /= 0) return
      inquire(unit=u, size=nb)
      if (nb <= 0) then
         close(u); return
      end if
      if (allocated(mbuf)) deallocate(mbuf)
      allocate(raw(nb), mbuf(nb))
      read(u, iostat=ios) raw
      close(u)
      if (ios /= 0) return
      do i = 1, nb
         v = int(raw(i))
         if (v < 0) v = v + 256
         mbuf(i) = v
      end do
      deallocate(raw)
      ok = .true.
   end subroutine mload

   ! Read a variable-length quantity at mbuf(p); advance p.
   subroutine gvlq(p, val)
      integer, intent(inout) :: p
      integer, intent(out) :: val
      integer :: b
      val = 0
      do
         b = mbuf(p)
         p = p + 1
         val = val * 128 + mod(b, 128)
         if (b < 128) exit
      end do
   end subroutine gvlq

   subroutine mparse(ok)
      logical, intent(out) :: ok
      integer :: p, ntrk, t, endp, atick, stat, b, dt, mt, ln, hi, nd, tmp
      ok = .false.
      if (nb < 14) return
      if (mbuf(1) /= 77 .or. mbuf(2) /= 84 .or. mbuf(3) /= 104 .or. &
          mbuf(4) /= 100) return
      ntrk = mbuf(11) * 256 + mbuf(12)
      division = mbuf(13) * 256 + mbuf(14)
      if (division <= 0) return
      if (allocated(et)) deallocate(et, ek, e0, e1, e2, enb, etp)
      allocate(et(nb), ek(nb), e0(nb), e1(nb), e2(nb), enb(nb), etp(nb))
      p = 15
      ne = 0
      do t = 1, ntrk
         if (p + 7 > nb) exit
         if (mbuf(p) /= 77 .or. mbuf(p+1) /= 84 .or. mbuf(p+2) /= 114 .or. &
             mbuf(p+3) /= 107) exit
         ln = mbuf(p+4)*16777216 + mbuf(p+5)*65536 + mbuf(p+6)*256 + mbuf(p+7)
         p = p + 8
         endp = p + ln
         atick = 0
         stat = 0
         do
            if (p >= endp) exit
            call gvlq(p, dt)
            atick = atick + dt
            b = mbuf(p)
            if (b >= 128) then
               stat = b
               p = p + 1
            end if
            if (stat == 255) then                 ! meta
               mt = mbuf(p); p = p + 1
               call gvlq(p, ln)
               if (mt == 81 .and. ln == 3) then
                  tmp = mbuf(p)*65536 + mbuf(p+1)*256 + mbuf(p+2)
                  ne = ne + 1
                  et(ne) = atick; ek(ne) = 1; etp(ne) = tmp
                  e0(ne) = 0; e1(ne) = 0; e2(ne) = 0; enb(ne) = 0
               end if
               if (mt == 47) then
                  p = endp; exit
               end if
               p = p + ln
            else if (stat == 240 .or. stat == 247) then   ! sysex
               call gvlq(p, ln)
               p = p + ln
            else                                   ! channel voice
               hi = (stat / 16) * 16
               if (hi == 192 .or. hi == 208) then
                  nd = 1
               else
                  nd = 2
               end if
               ne = ne + 1
               et(ne) = atick; ek(ne) = 0; e0(ne) = stat; e1(ne) = mbuf(p)
               if (nd == 2) then
                  e2(ne) = mbuf(p+1)
               else
                  e2(ne) = -1
               end if
               enb(ne) = nd; etp(ne) = 0
               p = p + nd
            end if
         end do
      end do
      ok = .true.
   end subroutine mparse

   ! Stable insertion sort of events by absolute tick (ties keep track order).
   subroutine msort()
      integer :: i, j, kt, kk, k0, k1, k2, kn, kp
      do i = 2, ne
         kt = et(i); kk = ek(i); k0 = e0(i); k1 = e1(i)
         k2 = e2(i); kn = enb(i); kp = etp(i)
         j = i - 1
         do
            if (j < 1) exit
            if (et(j) <= kt) exit
            et(j+1) = et(j); ek(j+1) = ek(j); e0(j+1) = e0(j); e1(j+1) = e1(j)
            e2(j+1) = e2(j); enb(j+1) = enb(j); etp(j+1) = etp(j)
            j = j - 1
         end do
         et(j+1) = kt; ek(j+1) = kk; e0(j+1) = k0; e1(j+1) = k1
         e2(j+1) = k2; enb(j+1) = kn; etp(j+1) = kp
      end do
   end subroutine msort

   ! Write one byte (0..255) to a stream unit.
   subroutine wbyte(u, v)
      integer, intent(in) :: u, v
      integer :: iv
      iv = v
      if (iv > 127) iv = iv - 256
      write(u) int(iv, int8)
   end subroutine wbyte

   ! Exact-byte dump to a file. which=0 -> play-mode wire stream; 1 -> raw .mid.
   subroutine mdump(fname, which, ok)
      character(len=*), intent(in) :: fname
      integer, intent(in) :: which
      logical, intent(out) :: ok
      integer :: u, ios, i, c
      ok = .false.
      open(newunit=u, file=fname, access='stream', form='unformatted', &
           status='replace', iostat=ios)
      if (ios /= 0) return
      if (which == 1) then
         do i = 1, nb
            call wbyte(u, mbuf(i))
         end do
      else
         do i = 1, ne
            if (ek(i) == 0) then
               call wbyte(u, e0(i))
               call wbyte(u, e1(i))
               if (enb(i) == 2) call wbyte(u, e2(i))
            end if
         end do
         do c = 0, 15
            call wbyte(u, 176 + c); call wbyte(u, 123); call wbyte(u, 0)
         end do
      end if
      close(u)
      ok = .true.
   end subroutine mdump

   ! Raw-dump the loaded .mid bytes to the live port.
   subroutine mraw_live()
      if (.not. ser_open) return
      call ser_write_raw(mbuf, nb)
   end subroutine mraw_live

   ! Play the parsed stream to the live port with (approximate) timing.
   subroutine mplay_live()
      integer :: i, last, delta, buf3(3), nbytes
      real(kind=8) :: tempo
      if (.not. ser_open) return
      tempo = 500000.0d0
      last = 0
      do i = 1, ne
         delta = et(i) - last
         last = et(i)
         if (delta > 0) call Sleep(int(nint(delta * tempo / division / 1000.0d0), c_int32_t))
         if (ek(i) == 1) then
            tempo = real(etp(i), 8)
         else
            buf3(1) = e0(i); buf3(2) = e1(i)
            nbytes = 2
            if (enb(i) == 2) then
               buf3(3) = e2(i); nbytes = 3
            end if
            call ser_write_raw(buf3, nbytes)
         end if
      end do
      do i = 0, 15
         buf3(1) = 176 + i; buf3(2) = 123; buf3(3) = 0
         call ser_write_raw(buf3, 3)
      end do
   end subroutine mplay_live

end module bdp_midi

! ============================================================================
program bdp_dock_pump_test
   use bdp_win32
   use bdp_catalog
   use bdp_midi
   implicit none

   character(len=256) :: line, tok, rest
   integer :: ios

   call init_eol()
   call build_catalog()

   print '(A)', 'RV2900WD Dock BDP Pump Test Tool  (Fortran / Win32)'
   print '(A)', 'Pump control per BDP Command Spec V2 -- 115200-8N1.'
   print '(A)', 'Type `help` for commands.'
   print '(A)', ''

   do
      write(*, '(A)', advance='no') 'bdp> '
      read(*, '(A)', iostat=ios) line
      if (ios /= 0) exit                     ! EOF (Ctrl-Z) ends the session
      call split_first(line, tok, rest)
      if (len_trim(tok) == 0) cycle
      call lower(tok)

      select case (trim(tok))
      case ('help', '?')
         call print_help()
      case ('quit', 'exit', 'q!')
         exit
      case ('ports')
         call list_ports()
      case ('open')
         if (len_trim(rest) == 0) then
            print '(A)', 'Usage:  open COMx'
         else if (connect(trim(adjustl(rest)))) then
            continue
         end if
      case ('close')
         call disconnect()
         print '(A)', 'Disconnected'
      case ('list')
         call print_catalog()
      case ('ending')
         call set_ending(trim(adjustl(rest)))
      case ('raw')
         if (len_trim(rest) > 0) call send_cmd(trim(adjustl(rest)))
      case ('ds1')
         call send_cmd('*DS1')
      case ('ds0')
         call send_cmd('*DS0')
      case ('dscheck')
         call send_cmd('?DS')
      case ('q')
         call do_query(rest)
      case ('s')
         call do_send(rest)
      case ('x')
         call do_stop(rest)
      case ('midi')
         call do_midi(rest)
      case ('mraw')
         if (.not. loaded) then
            print '(A)', 'Load a file first:  midi <file.mid>'
         else if (.not. ser_open) then
            print '(A)', 'Not connected.  open COMx  first.'
         else
            call mraw_live()
            print '(A,I0,A)', 'Sent ', nb, ' raw bytes.'
         end if
      case ('mplay')
         if (.not. loaded) then
            print '(A)', 'Load a file first:  midi <file.mid>'
         else if (.not. ser_open) then
            print '(A)', 'Not connected.  open COMx  first.'
         else
            print '(A)', 'Playing MIDI...'
            call mplay_live()
            print '(A)', 'Playback done.'
         end if
      case ('mdump')
         call do_mdump(rest, 0)
      case ('mdumpr')
         call do_mdump(rest, 1)
      case default
         print '(A)', 'Unknown command: ' // trim(tok) // '   (try `help`)'
      end select
   end do

   call disconnect()
   print '(A)', 'Bye.'

contains

   subroutine print_help()
      print '(A)', 'Commands:'
      print '(A)', '  ports              list COM ports'
      print '(A)', '  open COMx          connect (115200-8N1)'
      print '(A)', '  close              disconnect'
      print '(A)', '  list               show the test catalog with indices'
      print '(A)', '  q N                query test N            (sends ?XX)'
      print '(A)', '  s N [param]        send/write test N       (sends *XX<param>)'
      print '(A)', '  x N                stop test N             (sends the *XX off form)'
      print '(A)', '  ds1 | ds0 | dscheck   enter / exit / confirm test mode'
      print '(A)', '  raw <text>         send a literal command'
      print '(A)', '  ending cr|lf|crlf|none   set line ending (default cr)'
      print '(A)', '  midi <file>        load a .mid file'
      print '(A)', '  mraw               dump loaded .mid bytes raw to port'
      print '(A)', '  mplay              play loaded .mid (timed) to port'
      print '(A)', '  mdump <file>       write parsed wire stream to a file'
      print '(A)', '  mdumpr <file>      write raw .mid bytes to a file'
      print '(A)', '  help | quit'
   end subroutine print_help

   subroutine do_midi(rest)
      character(len=*), intent(in) :: rest
      character(len=256) :: fn, tail
      logical :: ok
      call split_first(rest, fn, tail)
      if (len_trim(fn) == 0) then
         print '(A)', 'Usage:  midi <file.mid>'
         return
      end if
      call mload(trim(fn), ok)
      if (.not. ok) then
         print '(A)', 'Could not open that file.'
         loaded = .false.
         return
      end if
      call mparse(ok)
      if (.not. ok) then
         print '(A)', 'Not a valid Standard MIDI File.'
         loaded = .false.
         return
      end if
      call msort()
      loaded = .true.
      print '(A,I0,A,I0,A,I0)', 'Loaded ', nb, ' bytes,  events=', ne, &
            ',  div=', division
   end subroutine do_midi

   subroutine do_mdump(rest, which)
      character(len=*), intent(in) :: rest
      integer, intent(in) :: which
      character(len=256) :: fn, tail
      logical :: ok
      if (.not. loaded) then
         print '(A)', 'Load a file first:  midi <file.mid>'
         return
      end if
      call split_first(rest, fn, tail)
      if (len_trim(fn) == 0) then
         print '(A)', 'Usage:  mdump <outfile>'
         return
      end if
      call mdump(trim(fn), which, ok)
      if (ok) then
         print '(A)', 'Wrote output file.'
      else
         print '(A)', 'Could not write output file.'
      end if
   end subroutine do_mdump

   subroutine print_catalog()
      integer :: i
      do i = 1, NTESTS
         if (len_trim(tests(i)%name) == 0) cycle
         print '(I3,A,A)', i, '  ', trim(tests(i)%name)
      end do
   end subroutine print_catalog

   ! Resolve "N" from a command tail into a valid catalog index, or -1.
   integer function pick(rest)
      character(len=*), intent(in) :: rest
      character(len=64) :: numtok, tail
      integer :: ios2, idx
      pick = -1
      call split_first(rest, numtok, tail)
      if (len_trim(numtok) == 0) then
         print '(A)', 'Need a test number. Try `list`.'
         return
      end if
      read(numtok, *, iostat=ios2) idx
      if (ios2 /= 0 .or. idx < 1 .or. idx > NTESTS) then
         print '(A)', 'Bad test number. Try `list`.'
         return
      end if
      pick = idx
   end function pick

   subroutine do_query(rest)
      character(len=*), intent(in) :: rest
      integer :: i
      i = pick(rest)
      if (i < 1) return
      if (len_trim(tests(i)%query) == 0) then
         print '(A)', 'Test ' // trim(tests(i)%name) // ' has no query command.'
         return
      end if
      call send_cmd(trim(tests(i)%query))
   end subroutine do_query

   subroutine do_send(rest)
      character(len=*), intent(in) :: rest
      character(len=64) :: numtok, param, cmd
      integer :: i, ios2, idx
      call split_first(rest, numtok, param)
      read(numtok, *, iostat=ios2) idx
      if (ios2 /= 0 .or. idx < 1 .or. idx > NTESTS) then
         print '(A)', 'Usage:  s N [param]   (see `list`)'
         return
      end if
      i = idx
      if (len_trim(tests(i)%wtpl) == 0) then
         print '(A)', 'Test ' // trim(tests(i)%name) // ' has no write command.'
         return
      end if
      if (len_trim(param) == 0) param = tests(i)%pdef
      cmd = apply_param(trim(tests(i)%wtpl), trim(adjustl(param)))
      call send_cmd(trim(cmd))
   end subroutine do_send

   subroutine do_stop(rest)
      character(len=*), intent(in) :: rest
      integer :: i
      i = pick(rest)
      if (i < 1) return
      if (len_trim(tests(i)%stopc) == 0) then
         print '(A)', 'Test ' // trim(tests(i)%name) // ' has no stop command.'
         return
      end if
      call send_cmd(trim(tests(i)%stopc))
   end subroutine do_stop

   subroutine set_ending(name)
      character(len=*), intent(in) :: name
      character(len=8) :: nm
      nm = name
      call lower(nm)
      select case (trim(nm))
      case ('cr', '')
         eol_chars(1:1) = char(13); eol_len = 1
      case ('lf')
         eol_chars(1:1) = char(10); eol_len = 1
      case ('crlf')
         eol_chars(1:1) = char(13); eol_chars(2:2) = char(10); eol_len = 2
      case ('none')
         eol_len = 0
      case default
         print '(A)', 'Unknown ending. Use cr | lf | crlf | none.'
         return
      end select
      print '(A)', 'Line ending set to ' // trim(nm)
   end subroutine set_ending

   ! Split `s` into its first whitespace-delimited token and the remainder.
   subroutine split_first(s, first, rest)
      character(len=*), intent(in)  :: s
      character(len=*), intent(out) :: first, rest
      character(len=len(s)) :: t
      integer :: i
      t = adjustl(s)
      i = index(trim(t), ' ')
      if (i == 0) then
         first = trim(t)
         rest = ''
      else
         first = t(1:i-1)
         rest = adjustl(t(i+1:))
      end if
   end subroutine split_first

   subroutine lower(s)
      character(len=*), intent(inout) :: s
      integer :: i, c
      do i = 1, len_trim(s)
         c = ichar(s(i:i))
         if (c >= 65 .and. c <= 90) s(i:i) = char(c + 32)
      end do
   end subroutine lower

end program bdp_dock_pump_test
