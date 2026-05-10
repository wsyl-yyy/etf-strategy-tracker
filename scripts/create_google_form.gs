function createEtfTradeForm() {
  const form = FormApp.create('ETF量化策略成交回填');
  form.setDescription('用于手机回填ETF策略实际成交记录。提交后数据会写入绑定的Google Sheets，供GitHub Actions生成加密日报。');

  form.addDateItem()
    .setTitle('日期')
    .setRequired(true);

  form.addListItem()
    .setTitle('标的')
    .setChoiceValues(['563360', '588000'])
    .setRequired(true);

  form.addListItem()
    .setTitle('方向')
    .setChoiceValues(['买入', '卖出'])
    .setRequired(true);

  form.addListItem()
    .setTitle('策略模块')
    .setChoiceValues([
      'A500初始底仓',
      'A500常规网格',
      'A500底仓趋势止盈',
      'A500备用金',
      '科创50波段',
      '科创50止盈',
      '科创50移动止盈',
      '科创50风控减仓',
      '全局备用金',
      '其他'
    ])
    .setRequired(true);

  form.addTextItem()
    .setTitle('成交价')
    .setHelpText('填写成交均价，例如 1.032')
    .setRequired(true);

  form.addTextItem()
    .setTitle('成交金额')
    .setHelpText('填写成交金额，单位元，例如 600')
    .setRequired(true);

  form.addTextItem()
    .setTitle('成交份额')
    .setHelpText('填写实际成交份额，例如 500')
    .setRequired(true);

  form.addTextItem()
    .setTitle('交易费用')
    .setHelpText('填写佣金等费用，单位元；没有可填 0')
    .setRequired(true);

  form.addParagraphTextItem()
    .setTitle('备注')
    .setHelpText('可填写触发规则、人工复核原因或异常说明')
    .setRequired(false);

  const spreadsheet = SpreadsheetApp.create('ETF量化策略成交记录');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, spreadsheet.getId());

  Logger.log('Form edit URL: ' + form.getEditUrl());
  Logger.log('Form public URL: ' + form.getPublishedUrl());
  Logger.log('Sheet URL: ' + spreadsheet.getUrl());
  Logger.log('GOOGLE_SHEET_ID: ' + spreadsheet.getId());
}

