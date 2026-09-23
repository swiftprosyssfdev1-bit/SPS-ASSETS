SHEET_ORDER = [
    'Air Conditioner',
    'Biometric Device',
    'Bluetooth Device',
    'CPU - System Unit',
    'Employee',
    'Hard Disk',
    'Incident Register',
    'IT Vendor',
    'Keyboard',
    'Laptop',
    'Monitor',
    'Mouse',
    'Networking Equipment',
    'Other Asset',
    'Project Backup',
    'Project Details',
    'Software - OS License',
    'UPS',
    'Workstation',
]

SHEET_DATA = {
    'Air Conditioner': [
        ['Asset Tag', 'Name', 'Status', 'Notes'],
        ['A/C001', 'Onida', 'Other', 'Status note: 1.5 TON, ADMIN ROOM'],
        ['A/C002', 'Onida', 'Other', 'Status note: 1.5 TON, MIDDLE LEFT OF OFFICE'],
        ['A/C003(Rented)', 'Videocon', 'Other', 'Status note: 1 TON, HEAD OFFICE ROOM'],
    ],
    'Biometric Device': [
        ['Asset Tag', 'Name', 'Status', 'Device Type', 'Details'],
        ['B001', 'ESSL', 'Working', 'Biometrics', 'BPO Office'],
        ['B002', 'ESSL', 'Working', 'Biometrics', 'BPO Office'],
    ],
    'Bluetooth Device': [
        ['Bluetooth No', 'Devices', 'Brand', 'Remark', 'User Name'],
        ['BC001', 'Keyboard, Mouse and Bluetooth', 'logitech', 'in house', 'Bhavani'],
        ['BC002', 'Keyboard, Mouse and Bluetooth', 'logitech', 'Bluetooth connector not available', 'in Devaraj house'],
        ['BC003', 'Keyboard, Mouse and Bluetooth', 'Raapoo', 'mouse not working', 'in Devaraj house'],
    ],
    'CPU - System Unit': [
        [
            'System No', 'System Name', 'Password', 'OS Type', 'Processor',
            'System Type', 'Motherboard Type', 'HardDisk Name', 'HardDisk Serial No.',
            'Graphics Card Serial No.', 'HardDisk Size', 'RAM', 'RAM Model',
            'RAM Size', 'DVD', 'CD', 'Extra', 'Remark', 'Last Service Date',
            'Antivirus', 'Condition', 'Notes', 'others',
        ],
        [
            '001', 'swiftserver', None, None, None, None, None, None, None, None,
            None, None, 'Empty Chassis In Vinoth Place Cupboard', None, 'Empty',
            'Empty Chassis - Idle',
        ],
        [
            '002', 'SPSW010002', '$w!ft@Pr0&ys', 'Win 7 Ultimate x64 bit(Pirated)',
            'Intel® Core™ i3 CPU 530 @ 2.93GHz', 'Intel Server Board S3420GPV', None,
            None, None, None, 'Transcend DDR3 2. (2.00 GB)=4GB', None,
            'Display Problem(Idle)', None, 'working', 'Idle',
        ],
        [
            '003', 'SPSW010003', None,
            'Windows Server 2012 R2 Standarad x64 bit(Trail Version)',
            'Intel Xeon(R) CPU E3-1220v2 @3.10 Ghz', 'Intel Server Board S3420GPV',
            None, None, None, None, '6GB', None, None, None, 'working',
            'Provided to Vicky Sir for Guindy Branch',
        ],
    ],
    'Employee': [
        ['EmployeeName', 'Employee Id', 'Status'],
        ['Subulakshmi', '1006', 'Active'],
        ['Rajeswari K', 'EMPLOYEE-R116', 'Resigned'],
        ['Mohan Thass Shanmugam', 'SPS002', 'Active'],
    ],
    'Hard Disk': [
        [
            'Hard disk  Number', 'Hard Disk Name', 'Size', 'Serial No',
            'Conditions', 'Purpose', 'Status', 'Current Status',
        ],
        ['ANT Esports', 'Internal Harddisk', '240 SSD', '8906136070950', 'Recived From 16-05-2026 Amazon', None, 'Working', None],
        ['CL001', 'Samsung', '2TB', 'E2F2JJHF305', 'problem with the port frequently disconnecting', None, 'not working & inside the cupboard', 'In Cupboard'],
        ['CL002', 'Toshiba', '500GB', '93FZT9TOTXR6', 'Given for service on 23052016 and came back not working so internal harddisk changed to 500gb original 2TB is in Waste storage', 'allocated for neps', 'working', 'Dhamu File Backup Working on Bpo Office in cupboard'],
    ],
    'Incident Register': [
        ['S.No', 'Incident Type', 'Incident Description', 'Start Date&Time', 'End Date&Time'],
        ['1', 'Natutral disaster', 'Vardha Storm attack chennai and all network got disconnected', '2016-12-12 00:00:00', '2016-12-13 00:00:00'],
        ['2', 'Network Disconnection', 'Airtel', '2015-12-14 00:00:00', '2015-12-14 00:00:00'],
        ['3', 'Network Disconnection', 'ACT1 and ACT2 Disconnection due to vardha Strom because of optical disconnection', '2016-12-12 00:00:00', '2016-03-10 00:00:00'],
    ],
    'IT Vendor': [
        ['S.No', 'Vendor Name', 'Contact Person', 'Mobile No', 'Types of Service', 'Details 1', 'Details 2'],
        ['1', 'Net4india', '044-2833510,044-2833530', '9840870874 - vijay, 9840096273 - Rajesh', 'Leaseline (2mbps)', None, None],
        ['10', 'Kriloskar', 'John, Navanitha Krishnan for service', 'Hohn - 9840338989, Navanitha Krishnan - 9176628497', 'Genset', '42033316', 'Service : 9444990295'],
        ['11', 'Building Electrician', 'Munna', '9789880051-prim, 9841435251 - sec', 'Electrician', None, None],
    ],
    'Keyboard': [
        ['Keyboard Id', 'Brand', 'Status', 'S.No', 'Location'],
        ['KB001', 'APPLE', 'Working', 'KB-AP-01', 'In Guindy'],
        ['K002', 'Microsoft', 'Working', 'KB-MS-02', 'In Cupboard'],
        ['K003', 'Microsoft', 'Working', 'KB-MS-03', 'In Cupboard'],
    ],
    'Laptop': [
        ['Asset Tag', 'Name', 'Brand', 'Serial Number', 'Status', 'Current Location', 'Notes'],
        ['LT001', 'Dell Latitude 3420', 'Dell', 'DL-88231', 'Working', 'Office Floor', 'Assigned to Dev team'],
    ],
    'Monitor': [
        ['Monitor No', 'Brand', 'Type', 'Color', 'Size', 'Model', 'Serial No', 'Condition'],
        ['M001', 'Acer', 'LCD', 'Black', '17 inch', 'AL1516W', '73503405043', 'working'],
        ['M007', 'Acer', 'LCD', 'Black', '17 inch', 'X193HQA', 'ETLEK0D025839041D98521', 'missing'],
        ['M008', 'Samsung', 'LCD', 'Black', '17 Inch', 'SyncMaster E1720', 'V8BYH9NB400727T', 'working but no stand'],
    ],
    'Mouse': [
        ['Mouse', 'Brand', 'Status', 'S.No', 'Location'],
        ['MS001', 'APPLE', 'Working', 'MS-AP-01', 'Guindy Office'],
        ['R001', 'Microsoft', 'Working', 'MS-MS-01', 'Used by Bharath Kumar'],
        ['R002', 'Microsoft', 'Working', 'MS-MS-02', 'Used by Geethanjalai'],
    ],
    'Networking Equipment': [
        ['Asset Tag', 'Name', 'Brand', 'Serial Number', 'Status', 'Current Location', 'Notes'],
        ['NET001', 'Cisco 24 Port Switch', 'Cisco', 'CS-99120', 'Working', 'Server Room', 'Main rack switch'],
    ],
    'Other Asset': [
        ['Asset Tag', 'Name', 'Brand', 'Serial Number', 'Status', 'Current Location', 'Notes'],
        ['TV001', '55 inch TV', 'TCL', 'TCL-55-01', 'Working', 'Meeting Room', 'With Logitech camera'],
        ['C001', 'Security Camera', 'MX', 'MX-C001', 'Working', 'BPO Office', 'Main entrance'],
        ['C002', 'Security Camera', 'MX', 'MX-C002', 'Working', 'BPO Office', 'Server aisle'],
    ],
    'Project Backup': [
        ['Hard disk Name', 'Projects', 'Project Manager', 'Date', 'Space Free', 'Backup Available HDD'],
        ['CL002', 'Auto XML and Marketing', 'Dhamu', '2017', None, None],
        ['EH003', 'Koln', 'Vinoth', '2016-10-01 00:00:00', '159 GB', None],
        ['EH003', 'Stampkort', 'Sreenivasulu / Murali', '2017', None, None],
    ],
    'Project Details': [
        ['S.No', 'Project Name', 'Status'],
        ['16', 'Alma Books', 'Working'],
        ['6', 'BASF', 'Working'],
        ['14', 'BKM', 'Working'],
    ],
    'Software - OS License': [
        ['Type', 'Version', 'Product Key', 'System No', 'Details'],
        ['Microsoft OS', 'Pro', '6FYFF-T2BWF-4CQTV-MXMTT-FT4YJ', '5 users', None],
        ['Microsoft OS', 'Pro', 'B7NGJ-3T9Q9-YKDTX-PVQHM-FGDGP', 'Amudhan', None],
        ['Microsoft OS', 'Server 2025', 'Q9TY6-7N6KT-HJGBD-Y3G7F-X2H7M', 'Ancestry Server', None],
    ],
    'UPS': [
        ['UPS No', 'Brand', 'Color', 'Model No', 'Size', 'Last Service Date', 'Condition', 'Details', 'Current Status'],
        ['U 0113 to 0117', 'Numeric', 'Black', '600VA', None, None, 'Working', 'Purchased on 11-06-2025', None],
        ['U001', '3PE', 'Black & Red', 'Sizzle-1000', 'Big Size', '-', 'destroyed', None, None],
        ['U002', '3PE', 'Black & White', 'BP-1200', 'Big Size', '-', 'destroyed', None, None],
    ],
    'Workstation': [
        [
            'Workstation ID', 'Employee ID', 'Employee Name', 'CPU Number',
            'Monitor Number', 'Keyboard Number', 'Mouse Number', 'UPS No.',
            'Product Id', 'Cd Key', 'Operating System', 'Purposes', 'User Id',
        ],
        ['WS001', 'TR1638', 'Nagoor Meeran', '049', 'M056', 'K029', 'R021', 'U0', '00426-OEM-8992662-00010', 'MHFPT-8C8M2-V9488-FGM44-2C9T3', 'Win 7 Ultimate x64 bit (Pirated)', 'System Administrator', 'SPSW070049'],
        ['WS002', 'TR1648', 'Aruna', '036', 'M009', 'K069', 'R053', 'U070', '00326-10000-00000-AA074', 'YTMG3-N6DKC-DKB77-7M9GH-8HVX7', 'Win 10 Home x64 bit', 'Hard Disk Sharing', 'SPS070036@outlook.com'],
    ],
}


def build_sample_workbook(headers_only=True):
    """Builds the Asset_Import_Template.xlsx workbook entirely in code (no
    static file on disk) and returns it as an openpyxl Workbook.

    Generates a clean, professional template matching all 19 asset categories
    with continuous columns and no empty spacer columns.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    header_fill = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    header_align = Alignment(vertical="center", wrap_text=False)

    wb = Workbook()
    wb.remove(wb.active)

    for name in SHEET_ORDER:
        ws = wb.create_sheet(name)
        rows_data = SHEET_DATA.get(name, [])
        rows = rows_data[:1] if headers_only else rows_data
        for row in rows:
            ws.append(row)

        header_row = rows_data[0] if rows_data else []
        for col_idx, value in enumerate(header_row, start=1):
            if value is None or str(value).strip() == "":
                continue
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align

            longest = len(str(value))
            for row in rows[1:]:
                if col_idx <= len(row) and row[col_idx - 1] is not None:
                    longest = max(longest, len(str(row[col_idx - 1])))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max(longest + 3, 12), 45)

        ws.freeze_panes = "A2"
        ws.row_dimensions[1].height = 20

    return wb


def build_sample_workbook_bytes(headers_only=True):
    """Returns the sample template workbook as raw .xlsx bytes, ready to be
    served directly in an HTTP response (no temp file needed)."""
    from io import BytesIO

    wb = build_sample_workbook(headers_only=headers_only)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()
